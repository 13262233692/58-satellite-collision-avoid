from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.config import settings

router = APIRouter()

_pipeline_cache: Dict[str, Any] = {}
_last_propagation_results: Optional[List[dict]] = None
_last_czml: Optional[str] = None
_last_warnings: Optional[List[dict]] = None


@router.get("/czml/stream", summary="获取 CZML 数据流")
async def get_czml_stream(
    refresh: bool = Query(False, description="强制重新计算"),
) -> StreamingResponse:
    global _last_czml, _last_propagation_results, _last_warnings

    if _last_czml and not refresh:
        return StreamingResponse(
            iter([_last_czml]),
            media_type="application/json",
            headers={
                "Content-Disposition": "attachment; filename=czml_data.json",
                "X-Data-Source": "cache",
            },
        )

    tle_path = settings.data_dir / "sample_tle.txt"
    if not tle_path.exists():
        raise HTTPException(status_code=404, detail="TLE data file not found")

    from app.tle.parser import parse_tle_file
    from app.propagation.sgp4_engine import propagate_batch
    from app.czml import build_czml, czml_to_json
    from app.tasks.orbit_tasks import detect_collision_warnings_task

    entries = parse_tle_file(str(tle_path), strict=False)
    tle_dicts = [
        {"tle_line1": e.line1, "tle_line2": e.line2}
        for e in entries
    ]

    start_time = datetime.now(timezone.utc)
    propagation_results = propagate_batch(tle_dicts, start_time)
    _last_propagation_results = [r.model_dump(mode="json") for r in propagation_results]

    czml_doc = build_czml(_last_propagation_results)
    _last_czml = czml_to_json(czml_doc)

    _last_warnings = detect_collision_warnings_task(_last_propagation_results)

    return StreamingResponse(
        iter([_last_czml]),
        media_type="application/json",
        headers={
            "Content-Disposition": "attachment; filename=czml_data.json",
            "X-Data-Source": "computed",
        },
    )


@router.get("/czml/refresh", summary="强制刷新 CZML 数据")
async def refresh_czml() -> Dict[str, Any]:
    global _last_czml, _last_propagation_results, _last_warnings

    tle_path = settings.data_dir / "sample_tle.txt"
    if not tle_path.exists():
        raise HTTPException(status_code=404, detail="TLE data file not found")

    from app.tle.parser import parse_tle_file
    from app.propagation.sgp4_engine import propagate_batch
    from app.czml import build_czml, czml_to_json
    from app.tasks.orbit_tasks import detect_collision_warnings_task

    entries = parse_tle_file(str(tle_path), strict=False)
    tle_dicts = [
        {"tle_line1": e.line1, "tle_line2": e.line2}
        for e in entries
    ]

    start_time = datetime.now(timezone.utc)
    propagation_results = propagate_batch(tle_dicts, start_time)
    _last_propagation_results = [r.model_dump(mode="json") for r in propagation_results]

    czml_doc = build_czml(_last_propagation_results)
    _last_czml = czml_to_json(czml_doc)

    _last_warnings = detect_collision_warnings_task(_last_propagation_results)

    return {
        "status": "ok",
        "satellites": len(_last_propagation_results),
        "czml_size_bytes": len(_last_czml),
        "warnings": len(_last_warnings or []),
    }


@router.get("/collisions", summary="获取碰撞预警列表")
async def get_collision_warnings() -> List[dict]:
    global _last_warnings

    if _last_warnings is None:
        tle_path = settings.data_dir / "sample_tle.txt"
        if not tle_path.exists():
            raise HTTPException(status_code=404, detail="TLE data file not found")

        from app.tle.parser import parse_tle_file
        from app.propagation.sgp4_engine import propagate_batch
        from app.tasks.orbit_tasks import detect_collision_warnings_task

        entries = parse_tle_file(str(tle_path), strict=False)
        tle_dicts = [
            {"tle_line1": e.line1, "tle_line2": e.line2}
            for e in entries
        ]

        start_time = datetime.now(timezone.utc)
        propagation_results = propagate_batch(tle_dicts, start_time)
        prop_data = [r.model_dump(mode="json") for r in propagation_results]
        _last_warnings = detect_collision_warnings_task(prop_data)

    return _last_warnings


@router.get("/statistics", summary="获取轨道统计数据")
async def get_statistics() -> Dict[str, Any]:
    global _last_propagation_results

    if _last_propagation_results is None:
        tle_path = settings.data_dir / "sample_tle.txt"
        if not tle_path.exists():
            raise HTTPException(status_code=404, detail="TLE data file not found")

        from app.tle.parser import parse_tle_file, get_orbit_info

        entries = parse_tle_file(str(tle_path), strict=False)

        orbit_counts = {"LEO": 0, "MEO": 0, "GEO": 0, "HEO": 0}
        debris_count = 0
        total = len(entries)

        for entry in entries:
            info = get_orbit_info(entry)
            orbit_type = info["classification"]
            orbit_counts[orbit_type] = orbit_counts.get(orbit_type, 0) + 1
            if "DEB" in entry.name.upper() or "DEBRIS" in entry.name.upper():
                debris_count += 1

        return {
            "total_objects": total,
            "satellites": total - debris_count,
            "debris_count": debris_count,
            "orbit_distribution": orbit_counts,
            "collision_warnings": len(_last_warnings or []),
            "last_updated": datetime.now(timezone.utc).isoformat(),
        }

    orbit_counts = {"LEO": 0, "MEO": 0, "GEO": 0, "HEO": 0, "DEBRIS": 0}
    debris_count = 0

    for r in _last_propagation_results:
        name = r.get("name", "")
        orbit_type = r.get("orbit_type", "LEO")
        if "DEB" in name.upper() or "DEBRIS" in name.upper():
            orbit_counts["DEBRIS"] = orbit_counts.get("DEBRIS", 0) + 1
            debris_count += 1
        else:
            orbit_counts[orbit_type] = orbit_counts.get(orbit_type, 0) + 1

    return {
        "total_objects": len(_last_propagation_results),
        "satellites": len(_last_propagation_results) - debris_count,
        "debris_count": debris_count,
        "orbit_distribution": orbit_counts,
        "collision_warnings": len(_last_warnings or []),
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/satellites", summary="获取卫星列表")
async def get_satellites() -> List[dict]:
    global _last_propagation_results

    if _last_propagation_results is None:
        tle_path = settings.data_dir / "sample_tle.txt"
        if not tle_path.exists():
            raise HTTPException(status_code=404, detail="TLE data file not found")

        from app.tle.parser import parse_tle_file, get_orbit_info

        entries = parse_tle_file(str(tle_path), strict=False)
        satellites = []
        for entry in entries:
            info = get_orbit_info(entry)
            satellites.append({
                "norad_id": entry.norad_id,
                "name": entry.name,
                "orbit_type": info["classification"],
                "inclination": entry.inclination,
                "altitude_range_km": info["altitude_range_km"],
                "orbital_period_minutes": info["orbital_period_minutes"],
            })
        return satellites

    satellites = []
    for r in _last_propagation_results:
        name = r.get("name", "")
        orbit_type = r.get("orbit_type", "LEO")
        if "DEB" in name.upper() or "DEBRIS" in name.upper():
            orbit_type = "DEBRIS"
        satellites.append({
            "norad_id": r.get("norad_id", 0),
            "name": name,
            "orbit_type": orbit_type,
        })
    return satellites


@router.get("/satellites/{norad_id}", summary="获取单颗卫星轨迹详情")
async def get_satellite_detail(norad_id: int) -> dict:
    global _last_propagation_results

    if _last_propagation_results is None:
        raise HTTPException(status_code=404, detail="No propagation data available. Run /czml/refresh first.")

    for r in _last_propagation_results:
        if r.get("norad_id") == norad_id:
            positions = r.get("positions", [])
            return {
                "norad_id": r["norad_id"],
                "name": r.get("name", ""),
                "orbit_type": r.get("orbit_type", ""),
                "position_count": len(positions),
                "positions": positions[:60],
            }

    raise HTTPException(status_code=404, detail=f"Satellite NORAD {norad_id} not found")


@router.get("/health", summary="健康检查")
async def health_check() -> Dict[str, str]:
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/dashboard", summary="大屏页面", include_in_schema=False)
async def dashboard():
    frontend_dir = Path(__file__).resolve().parent.parent.parent.parent / "frontend"
    index_file = frontend_dir / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), media_type="text/html")
    raise HTTPException(status_code=404, detail="Frontend not found")
