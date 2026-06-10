from __future__ import annotations

from datetime import datetime, timezone
from math import floor
from typing import Any, Dict, List, Optional

import numpy as np
from celery import chord
from dateutil.parser import isoparse

from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.orbit_tasks.propagate_orbits_task")
def propagate_orbits_task(tle_entries: List[dict], start_time: str) -> List[dict]:
    """Propagate orbits for a batch of TLE entries starting from the given time.

    Args:
        tle_entries: List of dicts with 'tle_line1' and 'tle_line2' keys.
        start_time: ISO-8601 datetime string for propagation start.

    Returns:
        Serialized list of propagation results.
    """
    from app.propagation.sgp4_engine import propagate_batch

    dt = isoparse(start_time)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    results = propagate_batch(tle_entries, dt)
    return [r.model_dump(mode="json") for r in results]


@celery_app.task(name="app.tasks.orbit_tasks.generate_czml_task")
def generate_czml_task(propagation_data: dict) -> str:
    """Generate a CZML document from propagation results.

    Args:
        propagation_data: Serialized propagation results (list of result dicts).

    Returns:
        CZML document as a JSON string.
    """
    import json

    from app.czml import build_czml

    czml_doc = build_czml(propagation_data)
    return json.dumps(czml_doc)


def _compute_spatial_grid(
    positions: np.ndarray, threshold_km: float
) -> Dict[tuple, List[int]]:
    """Assign position indices to spatial grid cells.

    Args:
        positions: Nx3 array of ECEF positions in km.
        threshold_km: Cell size in km (matches collision threshold).

    Returns:
        Dict mapping (cell_x, cell_y, cell_z) to list of object indices.
    """
    cells: Dict[tuple, List[int]] = {}
    inv_cell = 1.0 / threshold_km
    for idx in range(positions.shape[0]):
        key = (
            floor(positions[idx, 0] * inv_cell),
            floor(positions[idx, 1] * inv_cell),
            floor(positions[idx, 2] * inv_cell),
        )
        cells.setdefault(key, []).append(idx)
    return cells


def _check_cell_pairs(
    positions: np.ndarray,
    velocities: np.ndarray,
    indices: List[int],
    threshold_km: float,
    sat_ids: List[int],
    time_str: str,
    warnings: List[dict],
) -> None:
    """Check pairwise distances within a cell and record close approaches.

    Args:
        positions: Nx3 array of all positions.
        velocities: Nx3 array of all velocities.
        indices: Object indices within this cell.
        threshold_km: Distance threshold.
        sat_ids: NORAD IDs corresponding to each index.
        time_str: ISO time string for this step.
        warnings: List to append collision warnings to.
    """
    if len(indices) < 2:
        return

    idx_arr = np.array(indices)
    pos = positions[idx_arr]
    vel = velocities[idx_arr]
    ids = [sat_ids[i] for i in indices]

    diff = pos[:, np.newaxis, :] - pos[np.newaxis, :, :]
    dist = np.sqrt(np.sum(diff ** 2, axis=-1))

    vel_diff = vel[:, np.newaxis, :] - vel[np.newaxis, :, :]
    rel_speed = np.sqrt(np.sum(vel_diff ** 2, axis=-1))

    rows, cols = np.where((dist < threshold_km) & (dist > 0) & (np.triu(np.ones_like(dist), k=1) == 1))

    for r, c in zip(rows, cols):
        d = float(dist[r, c])
        rv = float(rel_speed[r, c])
        if d < 1.0:
            severity = "critical"
        elif d < 5.0:
            severity = "warning"
        else:
            severity = "caution"
        warnings.append(
            {
                "satellite1_id": ids[r],
                "satellite2_id": ids[c],
                "time": time_str,
                "distance_km": round(d, 4),
                "relative_velocity_kms": round(rv, 4),
                "severity": severity,
            }
        )


@celery_app.task(name="app.tasks.orbit_tasks.detect_collision_warnings_task")
def detect_collision_warnings_task(
    propagation_data: dict, threshold_km: float = 5.0
) -> List[dict]:
    """Detect close approaches between all objects using a spatial grid.

    Uses threshold_km-sized spatial cells so only objects in the same or
    adjacent cells are compared, achieving O(n) average complexity instead
    of O(n²) for pairwise checks.

    Args:
        propagation_data: Serialized propagation results mapping norad_id to
            position/velocity time series, or a list of propagation result dicts.
        threshold_km: Distance threshold in km for close approach detection.

    Returns:
        List of collision warning dicts.
    """
    results = propagation_data if isinstance(propagation_data, list) else propagation_data.get("results", [])

    if not results:
        return []

    time_steps: Dict[str, Dict[int, dict]] = {}
    for sat_result in results:
        norad_id = sat_result["norad_id"]
        for pv in sat_result["positions"]:
            t = pv["time"] if isinstance(pv["time"], str) else pv["time"].isoformat()
            time_steps.setdefault(t, {})[norad_id] = pv

    if not time_steps:
        return []

    sorted_times = sorted(time_steps.keys())
    warnings: List[dict] = []

    for t in sorted_times:
        step_data = time_steps[t]
        norad_ids = list(step_data.keys())
        n = len(norad_ids)
        if n < 2:
            continue

        positions = np.array(
            [
                [step_data[nid]["x_km"], step_data[nid]["y_km"], step_data[nid]["z_km"]]
                for nid in norad_ids
            ]
        )
        velocities = np.array(
            [
                [step_data[nid]["vx_kms"], step_data[nid]["vy_kms"], step_data[nid]["vz_kms"]]
                for nid in norad_ids
            ]
        )

        cells = _compute_spatial_grid(positions, threshold_km)
        checked: set = set()

        for cell_key, indices in cells.items():
            neighbor_indices: List[int] = list(indices)
            cx, cy, cz = cell_key
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        if dx == 0 and dy == 0 and dz == 0:
                            continue
                        neighbor = cells.get((cx + dx, cy + dy, cz + dz))
                        if neighbor is not None:
                            neighbor_indices.extend(neighbor)

            neighbor_indices = list(dict.fromkeys(neighbor_indices))
            pair_key = tuple(sorted(neighbor_indices))
            if pair_key in checked:
                continue
            checked.add(pair_key)

            _check_cell_pairs(
                positions, velocities, neighbor_indices, threshold_km, norad_ids, t, warnings
            )

    warnings.sort(key=lambda w: (w["time"], w["distance_km"]))
    return warnings


@celery_app.task(name="app.tasks.orbit_tasks.full_pipeline_task")
def full_pipeline_task(tle_file_path: str) -> dict:
    """Execute the full collision-avoidance pipeline.

    Reads and parses a TLE file, propagates all orbits, then generates
    CZML and collision warnings in parallel.

    Args:
        tle_file_path: Path to the TLE file to process.

    Returns:
        Dict with 'czml' (JSON string) and 'collision_warnings' (list).
    """
    from app.tle.parser import parse_tle_file

    entries = parse_tle_file(tle_file_path, strict=False)
    tle_dicts = [
        {"tle_line1": e.line1, "tle_line2": e.line2}
        for e in entries
    ]

    start_time = datetime.now(timezone.utc).isoformat()
    propagation_results = propagate_orbits_task(tle_dicts, start_time)

    czml_result = generate_czml_task(propagation_results)
    collision_warnings = detect_collision_warnings_task(propagation_results)

    return {
        "czml": czml_result,
        "collision_warnings": collision_warnings,
    }
