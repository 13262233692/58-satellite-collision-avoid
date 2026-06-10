from __future__ import annotations

import math
from typing import Optional

from pydantic import BaseModel


MU_EARTH = 398600.4418
EARTH_RADIUS_KM = 6371.0
G0 = 9.80665


class HohmannTransferResult(BaseModel):
    original_altitude_km: float
    target_altitude_km: float
    original_orbit_radius_km: float
    target_orbit_radius_km: float
    transfer_semi_major_km: float
    delta_v1_ms: float
    delta_v2_ms: float
    delta_v_total_ms: float
    transfer_time_seconds: float
    transfer_time_minutes: float
    fuel_kg: float
    initial_mass_kg: float
    dry_mass_kg: float
    isp_seconds: float
    delta_v1_fuel_kg: float
    delta_v2_fuel_kg: float
    burn1_duration_seconds: float
    burn2_duration_seconds: float
    burn1_attitude: str
    burn2_attitude: str
    elevation_height_km: float


class BurnDirective(BaseModel):
    burn_id: str
    label: str
    ignition_time: str
    delta_v_ms: float
    attitude: str
    attitude_degrees: dict
    duration_seconds: float
    fuel_kg: float
    thrust_N: float
    description: str


class ManeuverDirective(BaseModel):
    warning_id: str
    satellite_norad_id: int
    satellite_name: str
    debris_norad_id: int
    tca_time: str
    miss_distance_km: float
    severity: str
    strategy: str
    hohmann: HohmannTransferResult
    burn1: BurnDirective
    burn2: BurnDirective
    new_miss_distance_km: float
    safety_margin_km: float
    status: str


def hohmann_transfer(
    current_altitude_km: float,
    target_altitude_km: float,
    initial_mass_kg: float = 500.0,
    isp_seconds: float = 300.0,
    thrust_N: float = 20.0,
) -> HohmannTransferResult:
    r1 = EARTH_RADIUS_KM + current_altitude_km
    r2 = EARTH_RADIUS_KM + target_altitude_km
    a_t = (r1 + r2) / 2.0

    v_circular_1 = math.sqrt(MU_EARTH / r1)
    v_circular_2 = math.sqrt(MU_EARTH / r2)
    v_transfer_perigee = math.sqrt(MU_EARTH * (2.0 / r1 - 1.0 / a_t))
    v_transfer_apogee = math.sqrt(MU_EARTH * (2.0 / r2 - 1.0 / a_t))

    dv1 = abs(v_transfer_perigee - v_circular_1)
    dv2 = abs(v_circular_2 - v_transfer_apogee)
    dv_total = dv1 + dv2

    transfer_time = math.pi * math.sqrt(a_t ** 3 / MU_EARTH)

    dv1_ms = dv1 * 1000.0
    dv2_ms = dv2 * 1000.0
    dv_total_ms = dv_total * 1000.0

    mass_flow_rate = thrust_N / (isp_seconds * G0)

    mf1 = initial_mass_kg * math.exp(-dv1 / (isp_seconds * G0))
    fuel1 = initial_mass_kg - mf1

    mf2 = mf1 * math.exp(-dv2 / (isp_seconds * G0))
    fuel2 = mf1 - mf2

    total_fuel = fuel1 + fuel2

    burn1_duration = fuel1 / mass_flow_rate if mass_flow_rate > 0 else 0.0
    burn2_duration = fuel2 / mass_flow_rate if mass_flow_rate > 0 else 0.0

    return HohmannTransferResult(
        original_altitude_km=round(current_altitude_km, 2),
        target_altitude_km=round(target_altitude_km, 2),
        original_orbit_radius_km=round(r1, 2),
        target_orbit_radius_km=round(r2, 2),
        transfer_semi_major_km=round(a_t, 2),
        delta_v1_ms=round(dv1_ms, 4),
        delta_v2_ms=round(dv2_ms, 4),
        delta_v_total_ms=round(dv_total_ms, 4),
        transfer_time_seconds=round(transfer_time, 2),
        transfer_time_minutes=round(transfer_time / 60.0, 2),
        fuel_kg=round(total_fuel, 4),
        initial_mass_kg=initial_mass_kg,
        dry_mass_kg=round(mf2, 4),
        isp_seconds=isp_seconds,
        delta_v1_fuel_kg=round(fuel1, 4),
        delta_v2_fuel_kg=round(fuel2, 4),
        burn1_duration_seconds=round(burn1_duration, 2),
        burn2_duration_seconds=round(burn2_duration, 2),
        burn1_attitude="PROGRADE",
        burn2_attitude="PROGRADE",
        elevation_height_km=round(target_altitude_km - current_altitude_km, 2),
    )


def compute_burn_directives(
    hohmann: HohmannTransferResult,
    tca_time_str: str,
    warning_id: str,
    satellite_norad_id: int,
    thrust_N: float = 20.0,
) -> tuple:

    from datetime import datetime, timedelta, timezone

    try:
        tca = datetime.fromisoformat(tca_time_str)
        if tca.tzinfo is None:
            tca = tca.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        tca = datetime.now(timezone.utc)

    lead_time = timedelta(minutes=30)
    burn1_time = tca - lead_time
    burn2_time = burn1_time + timedelta(seconds=hohmann.transfer_time_seconds)

    mass_flow_rate = thrust_N / (hohmann.isp_seconds * G0)

    burn1 = BurnDirective(
        burn_id=f"BURN1-{warning_id}",
        label="第一次脉冲点火 · 轨道切入",
        ignition_time=burn1_time.isoformat(),
        delta_v_ms=hohmann.delta_v1_ms,
        attitude=hohmann.burn1_attitude,
        attitude_degrees={"yaw": 0, "pitch": 0, "roll": 0},
        duration_seconds=hohmann.burn1_duration_seconds,
        fuel_kg=hohmann.delta_v1_fuel_kg,
        thrust_N=thrust_N,
        description=(
            f"在 TCA 前 {int(lead_time.total_seconds()/60)} 分钟沿速度方向点火，"
            f"将卫星从 {hohmann.original_altitude_km:.0f} km 圆轨道切入至"
            f"半长轴 {hohmann.transfer_semi_major_km:.0f} km 的转移椭圆轨道。"
            f"速度增量 {hohmann.delta_v1_ms:.2f} m/s，"
            f"点火持续 {hohmann.burn1_duration_seconds:.1f} s，"
            f"消耗燃料 {hohmann.delta_v1_fuel_kg:.3f} kg。"
        ),
    )

    burn2 = BurnDirective(
        burn_id=f"BURN2-{warning_id}",
        label="第二次脉冲点火 · 轨道圆化",
        ignition_time=burn2_time.isoformat(),
        delta_v_ms=hohmann.delta_v2_ms,
        attitude=hohmann.burn2_attitude,
        attitude_degrees={"yaw": 0, "pitch": 0, "roll": 0},
        duration_seconds=hohmann.burn2_duration_seconds,
        fuel_kg=hohmann.delta_v2_fuel_kg,
        thrust_N=thrust_N,
        description=(
            f"在转移轨道远地点沿速度方向第二次点火，"
            f"将轨道圆化为 {hohmann.target_altitude_km:.0f} km 的安全圆轨道。"
            f"速度增量 {hohmann.delta_v2_ms:.2f} m/s，"
            f"点火持续 {hohmann.burn2_duration_seconds:.1f} s，"
            f"消耗燃料 {hohmann.delta_v2_fuel_kg:.3f} kg。"
        ),
    )

    return burn1, burn2


def estimate_altitude_from_tle(
    eccentricity: float,
    mean_motion: float,
) -> float:
    n_rad_s = mean_motion * 2.0 * math.pi / 86400.0
    semi_major = (MU_EARTH / (n_rad_s ** 2)) ** (1.0 / 3.0)
    perigee_alt = semi_major * (1.0 - eccentricity) - EARTH_RADIUS_KM
    return max(perigee_alt, 200.0)
