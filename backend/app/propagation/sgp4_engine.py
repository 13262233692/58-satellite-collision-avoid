from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

import numpy as np
from pydantic import BaseModel
from sgp4.api import Satrec, WGS72, jday
from sgp4.earth_gravity import wgs72
from sgp4.io import twoline2rv


class PositionVelocity(BaseModel):
    time: datetime
    x_km: float
    y_km: float
    z_km: float
    vx_kms: float
    vy_kms: float
    vz_kms: float


class PropagationResult(BaseModel):
    norad_id: int
    name: str
    orbit_type: str
    positions: List[PositionVelocity]


def compute_gmst(dt: datetime) -> float:
    """Compute Greenwich Mean Sidereal Time in degrees for a given UTC datetime."""
    j2000 = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    delta = dt - j2000
    d = delta.total_seconds() / 86400.0
    t = d / 36525.0
    theta = (
        280.46061837
        + 360.98564736629 * d
        + 0.000387933 * t * t
        - t * t * t / 38710000.0
    )
    theta = theta % 360.0
    if theta < 0:
        theta += 360.0
    return theta


def teme_to_ecef(
    x_teme: float, y_teme: float, z_teme: float, dt: datetime
) -> Tuple[float, float, float]:
    """Convert TEME coordinates to ECEF using GMST rotation around the Z axis."""
    theta = math.radians(compute_gmst(dt))
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    x_ecef = cos_t * x_teme + sin_t * y_teme
    y_ecef = -sin_t * x_teme + cos_t * y_teme
    z_ecef = z_teme
    return (x_ecef, y_ecef, z_ecef)


def _classify_orbit(eccentricity: float) -> str:
    """Classify orbit type based on eccentricity."""
    if eccentricity < 0.0 or eccentricity >= 1.0:
        return "unknown"
    if eccentricity < 0.01:
        return "LEO"
    if eccentricity < 0.1:
        return "MEO"
    return "HEO"


def _datetime_to_jd(dt: datetime) -> float:
    """Convert a datetime to a Julian date."""
    jd_array = jday(
        dt.year,
        dt.month,
        dt.day,
        dt.hour,
        dt.minute,
        dt.second + dt.microsecond * 1e-6,
    )
    return jd_array[0] + jd_array[1]


def propagate_single(
    tle_line1: str,
    tle_line2: str,
    start_time: datetime,
    duration_hours: int = 24,
    step_seconds: int = 60,
) -> PropagationResult:
    """Propagate a single satellite from TLE data over the given duration.

    Uses the SGP4 model to compute positions at each time step, converting
    from TEME to ECEF via GMST rotation.
    """
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)

    satellite = Satrec.twoline2rv(tle_line1, tle_line2, WGS72)

    norad_id = int(tle_line2[2:7].strip())
    name = f"NORAD {norad_id}"
    orbit_type = _classify_orbit(satellite.ecco)

    total_seconds = duration_hours * 3600
    num_steps = total_seconds // step_seconds

    times: List[datetime] = []
    julian_dates = np.empty(num_steps + 1)
    for i in range(num_steps + 1):
        dt = start_time + timedelta(seconds=i * step_seconds)
        times.append(dt)
        julian_dates[i] = _datetime_to_jd(dt)

    e, r, v = satellite.sgp4_array(julian_dates, np.zeros(num_steps + 1))

    positions: List[PositionVelocity] = []
    for i in range(num_steps + 1):
        if e[i] != 0:
            continue
        x_ecef, y_ecef, z_ecef = teme_to_ecef(r[i, 0], r[i, 1], r[i, 2], times[i])
        vx_ecef, vy_ecef, vz_ecef = teme_to_ecef(v[i, 0], v[i, 1], v[i, 2], times[i])
        positions.append(
            PositionVelocity(
                time=times[i],
                x_km=x_ecef,
                y_km=y_ecef,
                z_km=z_ecef,
                vx_kms=vx_ecef,
                vy_kms=vy_ecef,
                vz_kms=vz_ecef,
            )
        )

    return PropagationResult(
        norad_id=norad_id,
        name=name,
        orbit_type=orbit_type,
        positions=positions,
    )


def _propagate_worker(args: dict) -> PropagationResult:
    """Worker function for parallel batch propagation."""
    return propagate_single(
        tle_line1=args["tle_line1"],
        tle_line2=args["tle_line2"],
        start_time=args["start_time"],
        duration_hours=args.get("duration_hours", 24),
        step_seconds=args.get("step_seconds", 60),
    )


def propagate_batch(
    tle_entries: List[dict],
    start_time: datetime,
    duration_hours: int = 24,
    step_seconds: int = 60,
) -> List[PropagationResult]:
    """Propagate multiple satellites in parallel using a process pool.

    Each entry in tle_entries must be a dict with keys 'tle_line1' and 'tle_line2'.
    """
    worker_args = [
        {
            "tle_line1": entry["tle_line1"],
            "tle_line2": entry["tle_line2"],
            "start_time": start_time,
            "duration_hours": duration_hours,
            "step_seconds": step_seconds,
        }
        for entry in tle_entries
    ]

    results: List[PropagationResult] = []
    with ThreadPoolExecutor() as executor:
        futures = [executor.submit(_propagate_worker, a) for a in worker_args]
        for future in futures:
            results.append(future.result())

    return results
