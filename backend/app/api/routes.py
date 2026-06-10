from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

import time

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

_latest_batch_id: Optional[str] = None
_memory_czml: Optional[str] = None
_memory_warnings: Optional[List[dict]] = None
_memory_propagation: Optional[List[dict]] = None
_db_status: Optional[bool] = None
_db_status_ts: float = 0.0
_DB_CHECK_INTERVAL: float = 30.0


def _db_available() -> bool:
    global _db_status, _db_status_ts
    now = time.monotonic()
    if _db_status is not None and (now - _db_status_ts) < _DB_CHECK_INTERVAL:
        return _db_status
    try:
        import socket
        from app.config import settings as s
        sync_url = s.db.sync_url
        host_start = sync_url.find("@")
        if host_start == -1:
            host_start = sync_url.find("//") + 2
        else:
            host_start += 1
        host_end = sync_url.find(":", host_start)
        port_start = host_end + 1
        port_end = sync_url.find("/", port_start)
        host = sync_url[host_start:host_end]
        port = int(sync_url[port_start:port_end])
        with socket.create_connection((host, port), timeout=2):
            pass
        _db_status = True
    except Exception:
        _db_status = False
    _db_status_ts = now
    return _db_status


def _read_tle_entries() -> list:
    from app.tle.parser import parse_tle_file
    tle_path = settings.data_dir / "sample_tle.txt"
    if not tle_path.exists():
        raise HTTPException(status_code=404, detail="TLE data file not found")
    return parse_tle_file(str(tle_path), strict=False)


def _run_pipeline_sync() -> str:
    global _latest_batch_id, _memory_czml, _memory_warnings, _memory_propagation

    entries = _read_tle_entries()

    from app.tle.parser import get_orbit_info
    from app.propagation.sgp4_engine import propagate_batch
    from app.czml import build_czml, czml_to_json
    from app.tasks.orbit_tasks import detect_collision_warnings_task

    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
    start_time = datetime.now(timezone.utc)

    tle_dicts = [{"tle_line1": e.line1, "tle_line2": e.line2} for e in entries]
    propagation_results = propagate_batch(tle_dicts, start_time)
    prop_data = [r.model_dump(mode="json") for r in propagation_results]
    _memory_propagation = prop_data

    czml_doc = build_czml(prop_data)
    _memory_czml = czml_to_json(czml_doc)

    warning_result = detect_collision_warnings_task(
        {"batch_id": batch_id, "results": prop_data}, batch_id=batch_id
    )
    _memory_warnings = warning_result.get("warnings", [])

    if _db_available():
        try:
            from app.db import sync_session_scope, TLERepository, PropagationRepository, CZMLCacheRepository

            tle_dicts_for_db = []
            for e in entries:
                info = get_orbit_info(e)
                tle_dicts_for_db.append({
                    "norad_id": e.norad_id,
                    "name": e.name,
                    "classification": e.classification,
                    "line1": e.line1,
                    "line2": e.line2,
                    "inclination": e.inclination,
                    "raan": e.raan,
                    "eccentricity": e.eccentricity,
                    "arg_perigee": e.arg_perigee,
                    "mean_anomaly": e.mean_anomaly,
                    "mean_motion": e.mean_motion,
                    "orbit_type": info["classification"],
                })

            with sync_session_scope() as session:
                TLERepository.bulk_upsert(session, tle_dicts_for_db)
                PropagationRepository.save_results(session, batch_id, prop_data)
                CZMLCacheRepository.save(session, batch_id, _memory_czml, len(prop_data))

            logger.info("Batch %s: persisted to database", batch_id)
        except Exception:
            logger.exception("Batch %s: DB persist failed, using memory fallback", batch_id)

    _latest_batch_id = batch_id
    logger.info("Pipeline completed, batch_id=%s", batch_id)
    return batch_id


def _ensure_batch() -> str:
    global _latest_batch_id

    if _latest_batch_id is not None:
        if _db_available():
            try:
                from app.db import sync_readonly_session, CZMLCacheRepository
                with sync_readonly_session() as session:
                    cached = CZMLCacheRepository.get(session, _latest_batch_id)
                    if cached is not None:
                        return _latest_batch_id
            except Exception:
                pass
        elif _memory_czml is not None:
            return _latest_batch_id

    return _run_pipeline_sync()


@router.get("/czml/stream", summary="获取 CZML 数据流")
async def get_czml_stream(
    refresh: bool = Query(False, description="强制重新计算"),
) -> StreamingResponse:
    if not refresh:
        try:
            batch_id = _ensure_batch()

            if _db_available():
                from app.db import sync_readonly_session, CZMLCacheRepository
                with sync_readonly_session() as session:
                    czml_data = CZMLCacheRepository.get(session, batch_id)
                if czml_data:
                    return StreamingResponse(
                        iter([czml_data]),
                        media_type="application/json",
                        headers={
                            "Content-Disposition": "attachment; filename=czml_data.json",
                            "X-Data-Source": "db-cache",
                            "X-Batch-Id": batch_id,
                        },
                    )
            elif _memory_czml:
                return StreamingResponse(
                    iter([_memory_czml]),
                    media_type="application/json",
                    headers={
                        "Content-Disposition": "attachment; filename=czml_data.json",
                        "X-Data-Source": "memory-cache",
                        "X-Batch-Id": batch_id,
                    },
                )
        except Exception:
            logger.exception("Cache read failed, falling back to pipeline")

    batch_id = _run_pipeline_sync()

    czml_data = _memory_czml
    if _db_available():
        try:
            from app.db import sync_readonly_session, CZMLCacheRepository
            with sync_readonly_session() as session:
                czml_data = CZMLCacheRepository.get(session, batch_id) or czml_data
        except Exception:
            pass

    if czml_data is None:
        raise HTTPException(status_code=500, detail="CZML data generation failed")

    return StreamingResponse(
        iter([czml_data]),
        media_type="application/json",
        headers={
            "Content-Disposition": "attachment; filename=czml_data.json",
            "X-Data-Source": "computed",
            "X-Batch-Id": batch_id,
        },
    )


@router.get("/czml/refresh", summary="强制刷新 CZML 数据")
async def refresh_czml() -> Dict[str, Any]:
    batch_id = _run_pipeline_sync()
    return {
        "status": "ok",
        "batch_id": batch_id,
        "satellites": len(_memory_propagation) if _memory_propagation else 0,
        "czml_size_bytes": len(_memory_czml) if _memory_czml else 0,
        "warnings": len(_memory_warnings or []),
    }


@router.get("/collisions", summary="获取碰撞预警列表")
async def get_collision_warnings() -> List[dict]:
    _ensure_batch()
    if _db_available() and _latest_batch_id:
        try:
            from app.db import sync_readonly_session, CollisionWarningRepository
            with sync_readonly_session() as session:
                return CollisionWarningRepository.get_warnings_by_batch(session, _latest_batch_id)
        except Exception:
            pass
    return _memory_warnings or []


@router.get("/statistics", summary="获取轨道统计数据")
async def get_statistics() -> Dict[str, Any]:
    if _db_available():
        try:
            from app.db import sync_readonly_session, TLERepository, CollisionWarningRepository
            with sync_readonly_session() as session:
                stats = TLERepository.get_statistics(session)
                warning_count = 0
                if _latest_batch_id:
                    warning_count = CollisionWarningRepository.count_warnings(session, _latest_batch_id)
                stats["collision_warnings"] = warning_count
                stats["last_updated"] = datetime.now(timezone.utc).isoformat()
                return stats
        except Exception:
            logger.exception("DB statistics query failed, using fallback")

    entries = _read_tle_entries()
    from app.tle.parser import get_orbit_info
    orbit_counts: Dict[str, int] = {}
    debris_count = 0
    for e in entries:
        info = get_orbit_info(e)
        ot = info["classification"]
        orbit_counts[ot] = orbit_counts.get(ot, 0) + 1
        if "DEB" in e.name.upper() or "DEBRIS" in e.name.upper():
            debris_count += 1

    return {
        "total_objects": len(entries),
        "satellites": len(entries) - debris_count,
        "debris_count": debris_count,
        "orbit_distribution": orbit_counts,
        "collision_warnings": len(_memory_warnings or []),
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/satellites", summary="获取卫星列表")
async def get_satellites() -> List[dict]:
    if _db_available():
        try:
            from app.db import sync_readonly_session, TLERepository
            with sync_readonly_session() as session:
                return TLERepository.get_satellite_list(session)
        except Exception:
            logger.exception("DB satellite list query failed, using fallback")

    entries = _read_tle_entries()
    from app.tle.parser import get_orbit_info
    result = []
    for e in entries:
        info = get_orbit_info(e)
        orbit_type = "DEBRIS" if ("DEB" in e.name.upper() or "DEBRIS" in e.name.upper()) else info["classification"]
        result.append({
            "norad_id": e.norad_id,
            "name": e.name,
            "orbit_type": orbit_type,
            "inclination": e.inclination,
            "altitude_range_km": info["altitude_range_km"],
            "orbital_period_minutes": info["orbital_period_minutes"],
        })
    return result


@router.get("/satellites/{norad_id}", summary="获取单颗卫星轨迹详情")
async def get_satellite_detail(norad_id: int) -> dict:
    _ensure_batch()

    if _db_available() and _latest_batch_id:
        try:
            from app.db import sync_readonly_session, PropagationRepository
            with sync_readonly_session() as session:
                result = PropagationRepository.get_satellite_positions(session, _latest_batch_id, norad_id)
                if result is not None:
                    return result
        except Exception:
            pass

    if _memory_propagation:
        for r in _memory_propagation:
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
async def health_check() -> Dict[str, Any]:
    db_status = "connected" if _db_available() else "disconnected"

    return {
        "status": "healthy" if db_status == "connected" else "degraded",
        "database": db_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/dashboard", summary="大屏页面", include_in_schema=False)
async def dashboard():
    frontend_dir = Path(__file__).resolve().parent.parent.parent.parent / "frontend"
    index_file = frontend_dir / "index.html"
    if index_file.exists():
        return FileResponse(str(index_file), media_type="text/html")
    raise HTTPException(status_code=404, detail="Frontend not found")
