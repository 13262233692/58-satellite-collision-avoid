from pathlib import Path

from pydantic_settings import BaseSettings


class CelerySettings(BaseSettings):
    broker_url: str = "redis://localhost:6379/0"
    result_backend: str = "redis://localhost:6379/1"
    task_serializer: str = "orjson"
    result_serializer: str = "orjson"
    accept_content: list[str] = ["orjson", "json"]
    timezone: str = "UTC"
    enable_utc: bool = True
    task_track_started: bool = True

    model_config = {"env_prefix": "CELERY_"}


class SGP4Settings(BaseSettings):
    propagation_hours: int = 24
    step_minutes: float = 1.0
    steps: int = 1440

    model_config = {"env_prefix": "SGP4_"}


class CZMLSettings(BaseSettings):
    lead_time_minutes: int = 0
    trail_time_minutes: int = 0
    pixel_size: int = 10
    point_color: list[float] = [1.0, 1.0, 0.0, 1.0]
    path_color: list[float] = [1.0, 1.0, 0.0, 0.6]
    path_width: int = 1

    model_config = {"env_prefix": "CZML_"}


class APISettings(BaseSettings):
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = True
    cors_origins: list[str] = ["*"]
    api_prefix: str = "/api/v1"

    model_config = {"env_prefix": "API_"}


class Settings(BaseSettings):
    celery: CelerySettings = CelerySettings()
    sgp4: SGP4Settings = SGP4Settings()
    czml: CZMLSettings = CZMLSettings()
    api: APISettings = APISettings()

    data_dir: Path = Path(__file__).resolve().parent / "data"

    model_config = {"env_prefix": "APP_"}


settings = Settings()
