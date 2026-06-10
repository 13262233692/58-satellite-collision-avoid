from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.maneuver.hohmann import (
    BurnDirective,
    ManeuverDirective,
    estimate_altitude_from_tle,
    hohmann_transfer,
    compute_burn_directives,
)

logger = logging.getLogger(__name__)

DEFAULT_SATELLITE_MASS_KG = 500.0
DEFAULT_ISP_SECONDS = 300.0
DEFAULT_THRUST_N = 20.0
SAFETY_ALTITUDE_OFFSET_KM = 10.0


def generate_maneuver_for_warning(
    warning: dict,
    satellite_tle: Optional[dict] = None,
    satellite_mass_kg: float = DEFAULT_SATELLITE_MASS_KG,
    isp_seconds: float = DEFAULT_ISP_SECONDS,
    thrust_N: float = DEFAULT_THRUST_N,
) -> Optional[ManeuverDirective]:
    sat_norad = warning.get("satellite1_id", 0)
    debris_norad = warning.get("satellite2_id", 0)
    miss_distance = warning.get("distance_km", 999.0)
    tca_time = warning.get("time", datetime.now(timezone.utc).isoformat())
    severity = warning.get("severity", "caution")

    if satellite_tle is not None:
        current_alt = estimate_altitude_from_tle(
            satellite_tle.get("eccentricity", 0.001),
            satellite_tle.get("mean_motion", 15.5),
        )
    else:
        current_alt = _estimate_altitude_from_warning(warning)

    target_alt = current_alt + SAFETY_ALTITUDE_OFFSET_KM + miss_distance + 5.0

    hohmann = hohmann_transfer(
        current_altitude_km=current_alt,
        target_altitude_km=target_alt,
        initial_mass_kg=satellite_mass_kg,
        isp_seconds=isp_seconds,
        thrust_N=thrust_N,
    )

    warning_id = f"W-{sat_norad}-{debris_norad}-{uuid.uuid4().hex[:6]}"

    burn1, burn2 = compute_burn_directives(
        hohmann=hohmann,
        tca_time_str=tca_time,
        warning_id=warning_id,
        satellite_norad_id=sat_norad,
        thrust_N=thrust_N,
    )

    new_miss = miss_distance + hohmann.elevation_height_km + SAFETY_ALTITUDE_OFFSET_KM

    return ManeuverDirective(
        warning_id=warning_id,
        satellite_norad_id=sat_norad,
        satellite_name=satellite_tle.get("name", f"NORAD {sat_norad}") if satellite_tle else f"NORAD {sat_norad}",
        debris_norad_id=debris_norad,
        tca_time=tca_time,
        miss_distance_km=miss_distance,
        severity=severity,
        strategy="HOHMANN_ORBIT_RAISING",
        hohmann=hohmann,
        burn1=burn1,
        burn2=burn2,
        new_miss_distance_km=round(new_miss, 2),
        safety_margin_km=SAFETY_ALTITUDE_OFFSET_KM,
        status="PENDING_APPROVAL",
    )


def generate_maneuvers_for_warnings(
    warnings: List[dict],
    tle_lookup: Optional[Dict[int, dict]] = None,
    satellite_mass_kg: float = DEFAULT_SATELLITE_MASS_KG,
    isp_seconds: float = DEFAULT_ISP_SECONDS,
    thrust_N: float = DEFAULT_THRUST_N,
) -> List[ManeuverDirective]:
    maneuvers: List[ManeuverDirective] = []
    for w in warnings:
        sat_norad = w.get("satellite1_id", 0)
        tle = tle_lookup.get(sat_norad) if tle_lookup else None
        maneuver = generate_maneuver_for_warning(
            warning=w,
            satellite_tle=tle,
            satellite_mass_kg=satellite_mass_kg,
            isp_seconds=isp_seconds,
            thrust_N=thrust_N,
        )
        if maneuver is not None:
            maneuvers.append(maneuver)
    return maneuvers


def _estimate_altitude_from_warning(warning: dict) -> float:
    return 400.0


def generate_demo_maneuvers() -> List[dict]:
    now = datetime.now(timezone.utc)

    demo_warnings = [
        {
            "satellite1_id": 25544,
            "satellite2_id": 90001,
            "time": (now + timedelta(minutes=5)).isoformat(),
            "distance_km": 0.8,
            "severity": "critical",
        },
        {
            "satellite1_id": 44713,
            "satellite2_id": 90002,
            "time": (now + timedelta(minutes=20)).isoformat(),
            "distance_km": 3.2,
            "severity": "warning",
        },
        {
            "satellite1_id": 48274,
            "satellite2_id": 90003,
            "time": (now + timedelta(minutes=45)).isoformat(),
            "distance_km": 7.5,
            "severity": "caution",
        },
    ]

    tle_lookup = {
        25544: {"name": "ISS (ZARYA)", "eccentricity": 0.0007, "mean_motion": 15.4956},
        44713: {"name": "STARLINK-1234", "eccentricity": 0.0002, "mean_motion": 15.0642},
        48274: {"name": "STARLINK-5678", "eccentricity": 0.0001, "mean_motion": 15.0240},
    }

    maneuvers = generate_maneuvers_for_warnings(
        demo_warnings,
        tle_lookup=tle_lookup,
    )

    return [m.model_dump(mode="json") for m in maneuvers]
