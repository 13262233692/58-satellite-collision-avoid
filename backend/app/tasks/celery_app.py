from __future__ import annotations

import socket

from celery import Celery

from app.config import settings


def _redis_available(host: str = "localhost", port: int = 6379, timeout: float = 1.0) -> bool:
    """Check if Redis is reachable by attempting a TCP connection."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (ConnectionRefusedError, OSError, TimeoutError):
        return False


_celery_settings = settings.celery

_is_redis_up = _redis_available()

celery_app = Celery(
    "satellite_collision",
    broker=_celery_settings.broker_url,
    backend=_celery_settings.result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=_celery_settings.timezone,
    enable_utc=_celery_settings.enable_utc,
    task_track_started=_celery_settings.task_track_started,
    task_always_eager=not _is_redis_up,
    task_eager_propagates=True,
    task_routes={
        "app.tasks.orbit_tasks.propagate_orbits_task": {"queue": "orbit"},
        "app.tasks.orbit_tasks.generate_czml_task": {"queue": "czml"},
        "app.tasks.orbit_tasks.detect_collision_warnings_task": {"queue": "orbit"},
        "app.tasks.orbit_tasks.full_pipeline_task": {"queue": "orbit"},
    },
    task_default_queue="default",
)

celery_app.autodiscover_tasks(["app.tasks"])
