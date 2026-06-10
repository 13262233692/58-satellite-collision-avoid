from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

import orjson

ORBIT_COLORS: Dict[str, List[int]] = {
    "LEO": [0, 212, 255, 255],
    "MEO": [0, 255, 136, 255],
    "GEO": [255, 200, 0, 255],
    "HEO": [255, 136, 0, 255],
    "DEBRIS": [255, 60, 60, 255],
}

ORBIT_PIXEL_SIZES: Dict[str, int] = {
    "LEO": 6,
    "MEO": 5,
    "GEO": 4,
    "HEO": 7,
    "DEBRIS": 5,
}

ORBIT_PERIOD_SECONDS: Dict[str, float] = {
    "LEO": 5400.0,
    "MEO": 43000.0,
    "GEO": 86400.0,
    "HEO": 43000.0,
    "DEBRIS": 5400.0,
}

ALERT_COLORS = {
    "critical": [255, 0, 0, 255],
    "warning": [255, 255, 0, 255],
}


def _resolve_orbit_type(name: str, orbit_type: str) -> str:
    upper = name.upper()
    if "DEB" in upper or "DEBRIS" in upper:
        return "DEBRIS"
    return orbit_type


def _build_document_packet(start_time: datetime, end_time: datetime) -> Dict[str, Any]:
    return {
        "id": "document",
        "version": "1.0",
        "clock": {
            "interval": f"{start_time.isoformat()}/{end_time.isoformat()}",
            "currentTime": start_time.isoformat(),
            "multiplier": 60,
            "range": "LOOP_STOP",
            "step": "CLOCK_MULTIPLIER",
        },
    }


def _build_satellite_packet(
    result: Dict[str, Any],
    start_time: datetime,
    end_time: datetime,
) -> Dict[str, Any]:
    norad_id = result["norad_id"]
    name = result["name"]
    raw_orbit_type = result.get("orbit_type", "LEO")
    resolved_type = _resolve_orbit_type(name, raw_orbit_type)

    color = ORBIT_COLORS.get(resolved_type, ORBIT_COLORS["LEO"])
    pixel_size = ORBIT_PIXEL_SIZES.get(resolved_type, 6)
    period = ORBIT_PERIOD_SECONDS.get(resolved_type, 5400.0)

    positions = result.get("positions", [])
    cartesian_data: List[float] = []
    for pos in positions:
        t = pos["time"]
        if isinstance(t, str):
            t = datetime.fromisoformat(t)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        offset = (t - start_time).total_seconds()
        x_m = pos["x_km"] * 1000.0
        y_m = pos["y_km"] * 1000.0
        z_m = pos["z_km"] * 1000.0
        cartesian_data.extend([offset, x_m, y_m, z_m])

    satellite_id = f"satellite-{norad_id}"
    availability_start = start_time.isoformat()
    availability_end = end_time.isoformat()

    lead_time = period / 2.0
    trail_time = period / 2.0

    packet: Dict[str, Any] = {
        "id": satellite_id,
        "name": name,
        "availability": f"{availability_start}/{availability_end}",
        "position": {
            "interpolationDegree": 5,
            "interpolationAlgorithm": "LAGRANGE",
            "referenceFrame": "FIXED",
            "epoch": start_time.isoformat(),
            "cartesian": cartesian_data,
        },
        "point": {
            "pixelSize": pixel_size,
            "color": {"rgba": color},
        },
        "path": {
            "material": {
                "solidColor": {
                    "color": {"rgba": color},
                },
            },
            "width": 1,
            "leadTime": lead_time,
            "trailTime": trail_time,
            "resolution": 120,
        },
        "label": {
            "show": True,
            "text": name,
            "font": "11pt monospace",
            "style": "FILL_AND_OUTLINE",
            "outlineWidth": 1,
            "pixelOffset": {"cartesian2": [0, -12]},
            "scaleByDistance": {
                "nearFarScalar": [1.5e2, 1.5, 8.0e6, 0.4],
            },
            "translucencyByDistance": {
                "nearFarScalar": [1.5e2, 1.0, 8.0e6, 0.3],
            },
        },
        "properties": {
            "orbit_type": resolved_type,
            "norad_id": norad_id,
        },
    }

    return packet


def build_czml_document(
    propagation_results: List[Dict[str, Any]],
    start_time: datetime,
    end_time: datetime,
) -> List[Dict[str, Any]]:
    """Convert SGP4 propagation results into a Cesium CZML document.

    The first element is the document header packet with clock configuration.
    Subsequent elements are satellite packets with position, path, point,
    and label visualizations.
    """
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=timezone.utc)

    czml: List[Dict[str, Any]] = [_build_document_packet(start_time, end_time)]

    for result in propagation_results:
        packet = _build_satellite_packet(result, start_time, end_time)
        czml.append(packet)

    return czml


def build_czml_collision_alerts(alerts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create CZML packets for collision alert visualization.

    Each alert produces a polyline connecting two approaching satellites and
    a label at the midpoint showing the predicted miss distance.

    Alert dict keys:
        id: unique alert identifier
        satellite_a: id string of first satellite (e.g. "satellite-12345")
        satellite_b: id string of second satellite
        severity: "critical" or "warning"
        time_of_closest_approach: ISO datetime string
        miss_distance_km: float
        position_a: [x_m, y_m, z_m] ECEF position of satellite A at TCA
        position_b: [x_m, y_m, z_m] ECEF position of satellite B at TCA
    """
    packets: List[Dict[str, Any]] = []

    for alert in alerts:
        alert_id = alert.get("id", "alert-0")
        sat_a = alert["satellite_a"]
        sat_b = alert["satellite_b"]
        severity = alert.get("severity", "warning")
        miss_distance_km = alert.get("miss_distance_km", 0.0)
        pos_a = alert["position_a"]
        pos_b = alert["position_b"]
        tca = alert.get("time_of_closest_approach", "")

        color = ALERT_COLORS.get(severity, ALERT_COLORS["warning"])

        midpoint = [
            (pos_a[0] + pos_b[0]) / 2.0,
            (pos_a[1] + pos_b[1]) / 2.0,
            (pos_a[2] + pos_b[2]) / 2.0,
        ]

        polyline_packet: Dict[str, Any] = {
            "id": f"alert-line-{alert_id}",
            "name": f"Collision Alert {alert_id}",
            "polyline": {
                "positions": {
                    "cartesian": [
                        pos_a[0], pos_a[1], pos_a[2],
                        pos_b[0], pos_b[1], pos_b[2],
                    ],
                },
                "material": {
                    "solidColor": {
                        "color": {"rgba": color},
                    },
                },
                "width": 2,
                "clampToGround": False,
            },
        }
        packets.append(polyline_packet)

        label_packet: Dict[str, Any] = {
            "id": f"alert-label-{alert_id}",
            "name": f"Alert Distance {alert_id}",
            "position": {
                "cartesian": midpoint,
            },
            "label": {
                "show": True,
                "text": f"{miss_distance_km:.2f} km",
                "font": "12pt monospace",
                "style": "FILL_AND_OUTLINE",
                "outlineWidth": 2,
                "fillColor": {"rgba": color},
                "outlineColor": {"rgba": [0, 0, 0, 255]},
                "pixelOffset": {"cartesian2": [0, -8]},
                "scaleByDistance": {
                    "nearFarScalar": [1.5e2, 1.5, 8.0e6, 0.4],
                },
                "translucencyByDistance": {
                    "nearFarScalar": [1.5e2, 1.0, 8.0e6, 0.3],
                },
            },
        }
        packets.append(label_packet)

    return packets


def czml_to_json(czml: List[Dict[str, Any]]) -> str:
    """Serialize a CZML document to a JSON string using orjson."""
    return orjson.dumps(czml).decode("utf-8")
