from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from math import floor
from typing import Any, Dict, List, Optional

import numpy as np
from dateutil.parser import isoparse

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _generate_batch_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]


@celery_app.task(name="app.tasks.orbit_tasks.propagate_orbits_task", bind=True, max_retries=2)
def propagate_orbits_task(self, tle_entries: List[dict], start_time: str, batch_id: Optional[str] = None) -> dict:
    if batch_id is None:
        batch_id = _generate_batch_id()

    from app.propagation.sgp4_engine import propagate_batch
    from app.db import sync_session_scope, PropagationRepository, TLERepository

    dt = isoparse(start_time)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    results = propagate_batch(tle_entries, dt)
    serialized = [r.model_dump(mode="json") for r in results]

    try:
        with sync_session_scope() as session:
            tle_dicts = []
            for entry in tle_entries:
                norad_id = int(entry["tle_line2"][2:7].strip())
                tle_dicts.append({
                    "norad_id": norad_id,
                    "name": f"NORAD {norad_id}",
                    "line1": entry["tle_line1"],
                    "line2": entry["tle_line2"],
                })
            TLERepository.bulk_upsert(session, tle_dicts)
            PropagationRepository.save_results(session, batch_id, serialized)
            logger.info("Batch %s: saved %d propagation results", batch_id, len(serialized))
    except Exception:
        logger.exception("Batch %s: DB save failed, propagation data still returned", batch_id)

    return {"batch_id": batch_id, "results": serialized}


@celery_app.task(name="app.tasks.orbit_tasks.generate_czml_task", bind=True, max_retries=1)
def generate_czml_task(self, propagation_data: dict) -> dict:
    import json

    from app.czml import build_czml, czml_to_json
    from app.db import sync_session_scope, CZMLCacheRepository

    batch_id = propagation_data.get("batch_id", _generate_batch_id())
    results = propagation_data.get("results", propagation_data) if isinstance(propagation_data, dict) else propagation_data

    czml_doc = build_czml(results)
    czml_json = czml_to_json(czml_doc)

    try:
        with sync_session_scope() as session:
            CZMLCacheRepository.save(session, batch_id, czml_json, len(results) if isinstance(results, list) else 0)
            logger.info("Batch %s: CZML cached (%d bytes)", batch_id, len(czml_json))
    except Exception:
        logger.exception("Batch %s: CZML cache save failed", batch_id)

    return {"batch_id": batch_id, "czml": czml_json}


def _compute_spatial_grid(
    positions: np.ndarray, threshold_km: float
) -> Dict[tuple, List[int]]:
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


@celery_app.task(name="app.tasks.orbit_tasks.detect_collision_warnings_task", bind=True, max_retries=1)
def detect_collision_warnings_task(
    self, propagation_data: dict, threshold_km: float = 5.0, batch_id: Optional[str] = None
) -> dict:
    from app.db import sync_session_scope, CollisionWarningRepository

    if batch_id is None:
        batch_id = _generate_batch_id()

    results = propagation_data.get("results", propagation_data) if isinstance(propagation_data, dict) else propagation_data

    if not results:
        return {"batch_id": batch_id, "warnings": []}

    time_steps: Dict[str, Dict[int, dict]] = {}
    for sat_result in results:
        norad_id = sat_result["norad_id"]
        for pv in sat_result["positions"]:
            t = pv["time"] if isinstance(pv["time"], str) else pv["time"].isoformat()
            time_steps.setdefault(t, {})[norad_id] = pv

    if not time_steps:
        return {"batch_id": batch_id, "warnings": []}

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

    try:
        with sync_session_scope() as session:
            CollisionWarningRepository.save_warnings(session, batch_id, warnings)
            logger.info("Batch %s: saved %d collision warnings", batch_id, len(warnings))
    except Exception:
        logger.exception("Batch %s: collision warnings DB save failed", batch_id)

    return {"batch_id": batch_id, "warnings": warnings}


@celery_app.task(name="app.tasks.orbit_tasks.full_pipeline_task", bind=True, max_retries=1)
def full_pipeline_task(self, tle_file_path: str) -> dict:
    batch_id = _generate_batch_id()

    from app.tle.parser import parse_tle_file

    entries = parse_tle_file(tle_file_path, strict=False)
    tle_dicts = [
        {"tle_line1": e.line1, "tle_line2": e.line2}
        for e in entries
    ]

    start_time = datetime.now(timezone.utc).isoformat()

    propagation_result = propagate_orbits_task(tle_dicts, start_time, batch_id=batch_id)
    batch_id = propagation_result["batch_id"]

    czml_result = generate_czml_task(propagation_result)
    collision_result = detect_collision_warnings_task(propagation_result, batch_id=batch_id)

    return {
        "batch_id": batch_id,
        "czml": czml_result.get("czml", ""),
        "collision_warnings": collision_result.get("warnings", []),
    }
