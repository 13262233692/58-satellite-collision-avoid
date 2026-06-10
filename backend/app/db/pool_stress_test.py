"""
Connection pool stress test.

Simulates 50,000 debris objects being propagated, with each iteration
historically opening a raw DB connection. Now uses the singleton engine
with strict pool lifecycle management.

Usage:
    python -m app.db.pool_stress_test --objects 50000 --iterations 100
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import text

from app.db.engine import get_sync_engine, sync_readonly_session, sync_session_scope

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _simulate_worker_iteration(worker_id: int, iteration: int) -> dict:
    start = time.monotonic()
    try:
        with sync_readonly_session() as session:
            result = session.execute(text("SELECT 1")).scalar()
        elapsed = time.monotonic() - start
        return {"worker": worker_id, "iteration": iteration, "elapsed": elapsed, "ok": True}
    except Exception as e:
        elapsed = time.monotonic() - start
        return {"worker": worker_id, "iteration": iteration, "elapsed": elapsed, "ok": False, "error": str(e)}


def run_stress_test(num_objects: int, iterations: int, concurrency: int) -> None:
    engine = get_sync_engine()
    pool = engine.pool

    logger.info("=== Connection Pool Stress Test ===")
    logger.info("Objects: %d, Iterations: %d, Concurrency: %d", num_objects, iterations, concurrency)
    logger.info("Pool config: size=%d, max_overflow=%d, timeout=%.1fs, recycle=%ds",
                pool.size(), pool._max_overflow, pool.timeout(), pool._recycle)

    total_ops = num_objects * iterations
    logger.info("Total DB operations: %d", total_ops)

    completed = 0
    failed = 0
    total_time_start = time.monotonic()
    latencies: list[float] = []

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = []
        for obj_id in range(min(num_objects, concurrency)):
            for it in range(iterations):
                futures.append(
                    executor.submit(_simulate_worker_iteration, obj_id, it)
                )

        for future in as_completed(futures):
            result = future.result()
            completed += 1
            latencies.append(result["elapsed"])
            if not result["ok"]:
                failed += 1
                logger.error("FAIL worker=%d iter=%d: %s", result["worker"], result["iteration"], result.get("error", ""))

            if completed % 1000 == 0:
                pool_status = pool.status()
                logger.info("Progress: %d/%d completed, failed=%d, pool_status=%s",
                            completed, len(futures), failed, pool_status)

    total_time = time.monotonic() - total_time_start
    latencies.sort()

    logger.info("=== Results ===")
    logger.info("Total operations: %d", completed)
    logger.info("Failed: %d", failed)
    logger.info("Total time: %.2fs", total_time)
    logger.info("Throughput: %.0f ops/s", completed / total_time if total_time > 0 else 0)
    logger.info("Latency p50: %.4fs", latencies[len(latencies) // 2])
    logger.info("Latency p95: %.4fs", latencies[int(len(latencies) * 0.95)])
    logger.info("Latency p99: %.4fs", latencies[int(len(latencies) * 0.99)])
    logger.info("Pool final status: %s", pool.status())

    from app.db.engine import dispose_all_engines
    dispose_all_engines()


def main() -> None:
    parser = argparse.ArgumentParser(description="Connection pool stress test")
    parser.add_argument("--objects", type=int, default=50000, help="Number of simulated debris objects")
    parser.add_argument("--iterations", type=int, default=100, help="DB queries per object")
    parser.add_argument("--concurrency", type=int, default=50, help="Max concurrent threads")
    args = parser.parse_args()

    try:
        run_stress_test(args.objects, args.iterations, args.concurrency)
    except Exception:
        logger.exception("Stress test crashed")
        sys.exit(1)


if __name__ == "__main__":
    main()
