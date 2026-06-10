from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings


_frontend_dir = Path(__file__).resolve().parent.parent.parent / "frontend"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Satellite Collision Avoidance",
        description="Backend API for satellite orbit propagation and collision analysis",
        version="0.1.0",
        debug=settings.api.debug,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_routers(app)
    _mount_static_files(app)

    return app


def _register_routers(app: FastAPI) -> None:
    prefix = settings.api.api_prefix

    try:
        from app.api import router as api_router

        app.include_router(api_router, prefix=prefix)
    except ImportError:
        pass


def _mount_static_files(app: FastAPI) -> None:
    if _frontend_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")


app = create_app()
