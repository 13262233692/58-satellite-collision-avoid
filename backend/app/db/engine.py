from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Generator, Optional

from sqlalchemy import create_engine, event, pool
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

logger = logging.getLogger(__name__)


class _AsyncEngineSingleton:
    _instance: Optional[object] = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> object:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls._create()
        return cls._instance

    @classmethod
    def _create(cls) -> object:
        db = settings.db
        engine = create_async_engine(
            db.url,
            pool_size=db.pool_size,
            max_overflow=db.max_overflow,
            pool_timeout=db.pool_timeout,
            pool_recycle=db.pool_recycle,
            pool_pre_ping=db.pool_pre_ping,
            echo=db.echo,
        )

        @event.listens_for(engine.sync_engine, "checkout")
        def _on_checkout(dbapi_conn, connection_record, connection_proxy):
            logger.debug("Async pool checkout: %s", id(dbapi_conn))

        @event.listens_for(engine.sync_engine, "checkin")
        def _on_checkin(dbapi_conn, connection_record):
            logger.debug("Async pool checkin: %s", id(dbapi_conn))

        return engine

    @classmethod
    def dispose(cls) -> None:
        if cls._instance is not None:
            with cls._lock:
                if cls._instance is not None:
                    import asyncio
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            loop.create_task(cls._instance.dispose())
                        else:
                            loop.run_until_complete(cls._instance.dispose())
                    except RuntimeError:
                        pass
                    cls._instance = None


class _SyncEngineSingleton:
    _instance: Optional[object] = None
    _lock = threading.Lock()

    @classmethod
    def get(cls) -> object:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls._create()
        return cls._instance

    @classmethod
    def _create(cls) -> object:
        db = settings.db
        engine = create_engine(
            db.sync_url,
            pool_size=db.pool_size,
            max_overflow=db.max_overflow,
            pool_timeout=db.pool_timeout,
            pool_recycle=db.pool_recycle,
            pool_pre_ping=db.pool_pre_ping,
            echo=db.echo,
            connect_args={"connect_timeout": 3},
        )

        @event.listens_for(engine, "checkout")
        def _on_checkout(dbapi_conn, connection_record, connection_proxy):
            logger.debug("Sync pool checkout: %s", id(dbapi_conn))

        @event.listens_for(engine, "checkin")
        def _on_checkin(dbapi_conn, connection_record):
            logger.debug("Sync pool checkin: %s", id(dbapi_conn))

        return engine

    @classmethod
    def dispose(cls) -> None:
        if cls._instance is not None:
            with cls._lock:
                if cls._instance is not None:
                    cls._instance.dispose()
                    cls._instance = None


def get_async_engine():
    return _AsyncEngineSingleton.get()


def get_sync_engine():
    return _SyncEngineSingleton.get()


def get_async_session_factory() -> async_sessionmaker:
    return async_sessionmaker(
        get_async_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


def get_sync_session_factory() -> sessionmaker:
    return sessionmaker(
        get_sync_engine(),
        class_=Session,
        expire_on_commit=False,
        autoflush=False,
    )


@contextmanager
def sync_session_scope() -> Generator[Session, None, None]:
    session_factory = get_sync_session_factory()
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def sync_readonly_session() -> Generator[Session, None, None]:
    session_factory = get_sync_session_factory()
    session = session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_all_engines() -> None:
    _AsyncEngineSingleton.dispose()
    _SyncEngineSingleton.dispose()
