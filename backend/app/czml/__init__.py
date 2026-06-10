from app.czml.builder import (
    build_czml_collision_alerts,
    build_czml_document,
    czml_to_json,
)


def build_czml(propagation_data: dict) -> list:
    from datetime import datetime, timezone, timedelta

    results = propagation_data if isinstance(propagation_data, list) else propagation_data.get("results", [])
    if not results:
        return []

    all_times = []
    for r in results:
        for p in r.get("positions", []):
            t = p["time"]
            if isinstance(t, str):
                t = datetime.fromisoformat(t)
            all_times.append(t)

    if not all_times:
        return []

    start_time = min(all_times)
    end_time = max(all_times)

    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=timezone.utc)
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=timezone.utc)

    return build_czml_document(results, start_time, end_time)


__all__ = [
    "build_czml",
    "build_czml_document",
    "build_czml_collision_alerts",
    "czml_to_json",
]
