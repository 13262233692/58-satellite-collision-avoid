"""
Database migration script.

Usage:
    python -m app.db.migrate          # Create tables
    python -m app.db.migrate --drop   # Drop and recreate tables
"""
from __future__ import annotations

import sys

from sqlalchemy import text

from app.db.engine import get_sync_engine
from app.db.models import Base


def create_tables() -> None:
    engine = get_sync_engine()
    Base.metadata.create_all(engine)
    print("All tables created successfully.")

    with engine.connect() as conn:
        result = conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ))
        tables = [row[0] for row in result]
        print(f"Tables: {tables}")


def drop_tables() -> None:
    engine = get_sync_engine()
    Base.metadata.drop_all(engine)
    print("All tables dropped.")


def main() -> None:
    if "--drop" in sys.argv:
        drop_tables()
    create_tables()


if __name__ == "__main__":
    main()
