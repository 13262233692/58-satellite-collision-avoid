from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional

import numpy as np
import orjson
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db.engine import sync_readonly_session, sync_session_scope
from app.db.models import (
    CZMLCacheModel,
    CollisionWarningModel,
    PropagationResultModel,
    TLEEntryModel,
)

logger = logging.getLogger(__name__)


class TLERepository:

    @staticmethod
    def bulk_upsert(session: Session, entries: List[dict]) -> int:
        norad_ids = [e["norad_id"] for e in entries]
        existing = {
            r.norad_id: r
            for r in session.query(TLEEntryModel).filter(
                TLEEntryModel.norad_id.in_(norad_ids)
            ).all()
        }
        upserted = 0
        for entry in entries:
            model = existing.get(entry["norad_id"])
            if model is None:
                model = TLEEntryModel(
                    norad_id=entry["norad_id"],
                    name=entry["name"],
                    classification=entry.get("classification", "U"),
                    line1=entry["line1"],
                    line2=entry["line2"],
                    inclination=entry.get("inclination", 0.0),
                    raan=entry.get("raan", 0.0),
                    eccentricity=entry.get("eccentricity", 0.0),
                    arg_perigee=entry.get("arg_perigee", 0.0),
                    mean_anomaly=entry.get("mean_anomaly", 0.0),
                    mean_motion=entry.get("mean_motion", 0.0),
                    orbit_type=entry.get("orbit_type", "LEO"),
                    is_debris="DEB" in entry["name"].upper() or "DEBRIS" in entry["name"].upper(),
                )
                session.add(model)
            else:
                model.line1 = entry["line1"]
                model.line2 = entry["line2"]
                model.name = entry["name"]
                model.inclination = entry.get("inclination", model.inclination)
                model.raan = entry.get("raan", model.raan)
                model.eccentricity = entry.get("eccentricity", model.eccentricity)
                model.arg_perigee = entry.get("arg_perigee", model.arg_perigee)
                model.mean_anomaly = entry.get("mean_anomaly", model.mean_anomaly)
                model.mean_motion = entry.get("mean_motion", model.mean_motion)
                model.orbit_type = entry.get("orbit_type", model.orbit_type)
                model.is_debris = "DEB" in entry["name"].upper() or "DEBRIS" in entry["name"].upper()
                model.updated_at = datetime.now(timezone.utc)
            upserted += 1
        session.flush()
        return upserted

    @staticmethod
    def get_all_tle_pairs(session: Session) -> List[dict]:
        rows = session.query(TLEEntryModel).all()
        return [
            {
                "norad_id": r.norad_id,
                "name": r.name,
                "tle_line1": r.line1,
                "tle_line2": r.line2,
                "orbit_type": r.orbit_type,
                "is_debris": r.is_debris,
                "inclination": r.inclination,
            }
            for r in rows
        ]

    @staticmethod
    def get_tle_by_norad_ids(session: Session, norad_ids: List[int]) -> List[dict]:
        rows = session.query(TLEEntryModel).filter(
            TLEEntryModel.norad_id.in_(norad_ids)
        ).all()
        return [
            {
                "norad_id": r.norad_id,
                "name": r.name,
                "tle_line1": r.line1,
                "tle_line2": r.line2,
                "orbit_type": r.orbit_type,
            }
            for r in rows
        ]

    @staticmethod
    def get_statistics(session: Session) -> dict:
        total = session.query(func.count(TLEEntryModel.id)).scalar() or 0
        debris = session.query(func.count(TLEEntryModel.id)).filter(
            TLEEntryModel.is_debris == True
        ).scalar() or 0

        orbit_rows = session.query(
            TLEEntryModel.orbit_type, func.count(TLEEntryModel.id)
        ).group_by(TLEEntryModel.orbit_type).all()

        orbit_distribution = {row[0]: row[1] for row in orbit_rows}

        return {
            "total_objects": total,
            "satellites": total - debris,
            "debris_count": debris,
            "orbit_distribution": orbit_distribution,
        }

    @staticmethod
    def get_satellite_list(session: Session) -> List[dict]:
        rows = session.query(TLEEntryModel).all()
        result = []
        for r in rows:
            orbit_type = "DEBRIS" if r.is_debris else r.orbit_type
            result.append({
                "norad_id": r.norad_id,
                "name": r.name,
                "orbit_type": orbit_type,
                "inclination": r.inclination,
            })
        return result


class PropagationRepository:

    @staticmethod
    def save_results(session: Session, batch_id: str, results: List[dict], step_seconds: int = 60) -> int:
        for r in results:
            positions_data = r.get("positions", [])
            serialized = orjson.dumps(positions_data)

            model = PropagationResultModel(
                batch_id=batch_id,
                norad_id=r["norad_id"],
                name=r.get("name", ""),
                orbit_type=r.get("orbit_type", "LEO"),
                start_time=positions_data[0]["time"] if positions_data else datetime.now(timezone.utc),
                end_time=positions_data[-1]["time"] if positions_data else datetime.now(timezone.utc),
                step_seconds=step_seconds,
                position_count=len(positions_data),
                positions_blob=serialized,
            )
            session.add(model)
        session.flush()
        return len(results)

    @staticmethod
    def get_latest_batch_results(session: Session, batch_id: str) -> List[dict]:
        rows = session.query(PropagationResultModel).filter(
            PropagationResultModel.batch_id == batch_id
        ).all()
        results = []
        for row in rows:
            positions = orjson.loads(row.positions_blob) if row.positions_blob else []
            results.append({
                "norad_id": row.norad_id,
                "name": row.name,
                "orbit_type": row.orbit_type,
                "positions": positions,
            })
        return results

    @staticmethod
    def get_satellite_positions(session: Session, batch_id: str, norad_id: int) -> Optional[dict]:
        row = session.query(PropagationResultModel).filter(
            PropagationResultModel.batch_id == batch_id,
            PropagationResultModel.norad_id == norad_id,
        ).first()
        if row is None:
            return None
        positions = orjson.loads(row.positions_blob) if row.positions_blob else []
        return {
            "norad_id": row.norad_id,
            "name": row.name,
            "orbit_type": row.orbit_type,
            "position_count": row.position_count,
            "positions": positions[:60],
        }


class CollisionWarningRepository:

    @staticmethod
    def save_warnings(session: Session, batch_id: str, warnings: List[dict]) -> int:
        for w in warnings:
            tca = w["time"] if isinstance(w["time"], datetime) else datetime.fromisoformat(w["time"])
            model = CollisionWarningModel(
                batch_id=batch_id,
                satellite1_id=w["satellite1_id"],
                satellite2_id=w["satellite2_id"],
                time_of_closest_approach=tca,
                miss_distance_km=w["distance_km"],
                relative_velocity_kms=w.get("relative_velocity_kms", 0.0),
                severity=w["severity"],
            )
            session.add(model)
        session.flush()
        return len(warnings)

    @staticmethod
    def get_warnings_by_batch(session: Session, batch_id: str) -> List[dict]:
        rows = session.query(CollisionWarningModel).filter(
            CollisionWarningModel.batch_id == batch_id
        ).order_by(CollisionWarningModel.miss_distance_km).all()
        return [
            {
                "id": row.id,
                "satellite1_id": row.satellite1_id,
                "satellite2_id": row.satellite2_id,
                "time": row.time_of_closest_approach.isoformat(),
                "distance_km": row.miss_distance_km,
                "relative_velocity_kms": row.relative_velocity_kms,
                "severity": row.severity,
            }
            for row in rows
        ]

    @staticmethod
    def count_warnings(session: Session, batch_id: str) -> int:
        return session.query(func.count(CollisionWarningModel.id)).filter(
            CollisionWarningModel.batch_id == batch_id
        ).scalar() or 0


class CZMLCacheRepository:

    @staticmethod
    def save(session: Session, batch_id: str, czml_json: str, satellite_count: int) -> None:
        existing = session.query(CZMLCacheModel).filter(
            CZMLCacheModel.batch_id == batch_id
        ).first()
        if existing is not None:
            session.delete(existing)
            session.flush()
        model = CZMLCacheModel(
            batch_id=batch_id,
            czml_data=czml_json,
            size_bytes=len(czml_json.encode("utf-8")),
            satellite_count=satellite_count,
        )
        session.add(model)
        session.flush()

    @staticmethod
    def get(session: Session, batch_id: str) -> Optional[str]:
        row = session.query(CZMLCacheModel).filter(
            CZMLCacheModel.batch_id == batch_id
        ).first()
        return row.czml_data if row else None
