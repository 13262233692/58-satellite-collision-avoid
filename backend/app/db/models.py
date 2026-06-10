from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TLEEntryModel(Base):
    __tablename__ = "tle_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    norad_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    classification: Mapped[str] = mapped_column(String(1), nullable=False, default="U")
    line1: Mapped[str] = mapped_column(String(70), nullable=False)
    line2: Mapped[str] = mapped_column(String(70), nullable=False)
    inclination: Mapped[float] = mapped_column(Float, nullable=False)
    raan: Mapped[float] = mapped_column(Float, nullable=False)
    eccentricity: Mapped[float] = mapped_column(Float, nullable=False)
    arg_perigee: Mapped[float] = mapped_column(Float, nullable=False)
    mean_anomaly: Mapped[float] = mapped_column(Float, nullable=False)
    mean_motion: Mapped[float] = mapped_column(Float, nullable=False)
    orbit_type: Mapped[str] = mapped_column(String(16), nullable=False, default="LEO")
    is_debris: Mapped[bool] = mapped_column(default=False, nullable=False)
    epoch_year: Mapped[int] = mapped_column(Integer, nullable=True)
    epoch_day: Mapped[float] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        UniqueConstraint("norad_id", name="uq_tle_norad_id"),
        Index("ix_tle_orbit_type", "orbit_type"),
        Index("ix_tle_is_debris", "is_debris"),
    )


class PropagationResultModel(Base):
    __tablename__ = "propagation_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    norad_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    orbit_type: Mapped[str] = mapped_column(String(16), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    step_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    position_count: Mapped[int] = mapped_column(Integer, nullable=False)
    positions_blob: Mapped[bytes] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_prop_batch_norad", "batch_id", "norad_id"),
    )


class CollisionWarningModel(Base):
    __tablename__ = "collision_warnings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    satellite1_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    satellite2_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    time_of_closest_approach: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    miss_distance_km: Mapped[float] = mapped_column(Float, nullable=False)
    relative_velocity_kms: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_warning_batch_severity", "batch_id", "severity"),
        Index("ix_warning_tca", "time_of_closest_approach"),
    )


class CZMLCacheModel(Base):
    __tablename__ = "czml_cache"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    czml_data: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    satellite_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
