import uvicorn

from app.config import settings


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=settings.api.host,
        port=settings.api.port,
        reload=False,
    )


if __name__ == "__main__":
    main()
