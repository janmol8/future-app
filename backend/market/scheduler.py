"""Scheduler: APScheduler jobs guarded by DB leases, recorded in job_runs.

Safe with several processes: a job body runs only while its DB lease is held.
Jobs are registered by name in a registry; app wiring (Phase 2/3) supplies the
callables. ENABLE_SCHEDULER=0 disables startup entirely.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import socket
import threading

from .config import (
    ENABLE_SCHEDULER,
    INTERVAL_INDEX_REFRESH,
    INTERVAL_STATUS_REFRESH,
    INTERVAL_WATCHLIST_REFRESH,
    INTERVAL_UNIVERSE_REFRESH,
    INTERVAL_IPO_LIST_REFRESH,
    INTERVAL_GMP_REFRESH,
    INTERVAL_MASTER_REFRESH,
    INTERVAL_LISTING_DAY_LOOP,
    ALERT_SUBSCRIPTION_THROTTLE_MINUTES,
)
from .timeutil import iso_utc

logger = logging.getLogger(__name__)

JOB_REGISTRY: dict[str, callable] = {}


def register_job(name: str, fn: callable) -> None:
    JOB_REGISTRY[name] = fn


class _Holder:
    holder_id = f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"


def acquire_lease(conn, job_name: str, ttl_seconds: float) -> bool:
    """Try to take/refresh a lease. Returns True when this caller holds it."""
    now = iso_utc()
    later = iso_utc_from_seconds(ttl_seconds)
    row = conn.execute(
        "SELECT lease_until, holder FROM job_leases WHERE job_name = ?", (job_name,)
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO job_leases (job_name, lease_until, holder, acquired_at) VALUES (?,?,?,?)",
            (job_name, later, _Holder.holder_id, now),
        )
        return True
    if row["lease_until"] <= now or row["holder"] == _Holder.holder_id:
        conn.execute(
            "UPDATE job_leases SET lease_until = ?, holder = ?, acquired_at = ? WHERE job_name = ?",
            (later, _Holder.holder_id, now, job_name),
        )
        return True
    return False


def iso_utc_from_seconds(seconds: float) -> str:
    from datetime import datetime, timedelta, timezone
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).isoformat()


def release_lease(conn, job_name: str) -> None:
    """Release our own lease so the next scheduled tick can run immediately.
    The TTL only exists as a crash-safety net for holders that never release."""
    conn.execute(
        "DELETE FROM job_leases WHERE job_name = ? AND holder = ?",
        (job_name, _Holder.holder_id),
    )


def run_guarded(db, job_name: str, fn, lease_ttl: float = 60.0) -> str:
    """Run a job body under a DB lease; record the outcome in job_runs.
    Returns 'ok' | 'error' | 'skipped_locked'. The lease is released on
    completion; concurrent processes are excluded by the fresh-lease check."""
    started = iso_utc()
    conn = db.connect()
    try:
        if not acquire_lease(conn, job_name, lease_ttl):
            _record_run(conn, job_name, started, "skipped_locked", None, 0)
            conn.commit()
            return "skipped_locked"
    finally:
        conn.close()

    import time
    t0 = time.monotonic()
    try:
        fn()
        status, detail = "ok", None
    except Exception as exc:  # noqa: BLE001 — a job must never kill the scheduler
        logger.exception("job %s failed", job_name)
        status, detail = "error", f"{type(exc).__name__}: {exc}"
    duration_ms = int((time.monotonic() - t0) * 1000)

    conn = db.connect()
    try:
        release_lease(conn, job_name)
        _record_run(conn, job_name, started, status, detail, duration_ms)
        conn.commit()
    finally:
        conn.close()
    return status


def _record_run(conn, job_name, started_at, status, detail, duration_ms) -> None:
    conn.execute(
        "INSERT INTO job_runs (job_name, started_at, finished_at, status, detail, duration_ms)"
        " VALUES (?, ?, datetime('now'), ?, ?, ?)",
        (job_name, started_at, status, detail, duration_ms),
    )


class MarketScheduler:
    """Owns the APScheduler instance and the standard job set."""

    def __init__(self, db):
        self.db = db
        self._scheduler = None

    def start(self) -> bool:
        if not ENABLE_SCHEDULER:
            logger.info("scheduler disabled via ENABLE_SCHEDULER")
            return False
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
        except ImportError:
            logger.warning("APScheduler not installed; scheduler not started")
            return False

        self._scheduler = BackgroundScheduler(timezone="Asia/Kolkata", coalesce=True)
        jobs = [
            ("market_status", INTERVAL_STATUS_REFRESH),
            ("index_refresh", INTERVAL_INDEX_REFRESH),
            ("watchlist_refresh", INTERVAL_WATCHLIST_REFRESH),
            ("universe_refresh", INTERVAL_UNIVERSE_REFRESH),
            ("ipo_list_refresh", INTERVAL_IPO_LIST_REFRESH),
            ("gmp_refresh", INTERVAL_GMP_REFRESH),
            ("master_refresh", INTERVAL_MASTER_REFRESH),
            ("listing_day_loop", INTERVAL_LISTING_DAY_LOOP),
            ("alert_evaluate", 30),
            ("event_drain", max(60, ALERT_SUBSCRIPTION_THROTTLE_MINUTES * 60 // 4)),
        ]
        for name, seconds in jobs:
            fn = JOB_REGISTRY.get(name)
            if fn is None:
                continue
            interval_key = f"job:{name}"
            extra = {}
            if name == "ipo_list_refresh":
                # Detect IPOs right after startup instead of waiting a full interval.
                extra["next_run_time"] = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=8)
            self._scheduler.add_job(
                run_guarded,
                trigger="interval",
                seconds=max(5, seconds),
                args=(self.db, name, fn, max(60.0, seconds * 0.8)),
                id=interval_key,
                max_instances=1,
                misfire_grace_time=max(10, seconds // 2),
                **extra,
            )
        self._scheduler.start()
        logger.info("scheduler started with %d jobs", len(jobs))
        return True

    def shutdown(self) -> None:
        if self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
