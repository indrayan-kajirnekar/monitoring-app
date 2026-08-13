"""
scheduler.py — APScheduler-based email report scheduler for HyperMonitor.

Supports two schedule types:
  • "daily"   — runs every day at a fixed HH:MM UTC time
  • "weekly"  — runs every week on a fixed day (0=Mon … 6=Sun) at HH:MM UTC

The schedule is persisted in the email_schedule table (one row, id=1).
APScheduler stores jobs in memory only — the app re-registers from DB on
startup.

The actual report build + delivery is delegated to mailer.send_report()
which is called in a thread pool to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Optional

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Lazy APScheduler init
# ──────────────────────────────────────────────────────────────────────────────

_scheduler = None   # type: ignore


def _get_scheduler():
    global _scheduler
    if _scheduler is None:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

JOB_ID = "scheduled_report"


def start() -> None:
    sched = _get_scheduler()
    if not sched.running:
        sched.start()
        log.info("APScheduler started.")


def stop() -> None:
    sched = _get_scheduler()
    if sched.running:
        sched.shutdown(wait=False)
        log.info("APScheduler stopped.")


def remove_job() -> None:
    sched = _get_scheduler()
    if sched.get_job(JOB_ID):
        sched.remove_job(JOB_ID)
        log.info("Scheduled report job removed.")


def schedule_daily(hour: int, minute: int, job_fn: Callable) -> None:
    """Schedule job_fn to run every day at HH:MM UTC."""
    sched = _get_scheduler()
    remove_job()
    sched.add_job(
        job_fn,
        trigger="cron",
        id=JOB_ID,
        hour=hour,
        minute=minute,
        timezone="UTC",
        replace_existing=True,
        misfire_grace_time=300,
    )
    log.info("Daily report scheduled at %02d:%02d UTC", hour, minute)


def schedule_weekly(day_of_week: int, hour: int, minute: int,
                    job_fn: Callable) -> None:
    """
    Schedule job_fn to run every week.
    day_of_week: 0=Monday … 6=Sunday
    """
    days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
    sched = _get_scheduler()
    remove_job()
    sched.add_job(
        job_fn,
        trigger="cron",
        id=JOB_ID,
        day_of_week=days[day_of_week % 7],
        hour=hour,
        minute=minute,
        timezone="UTC",
        replace_existing=True,
        misfire_grace_time=600,
    )
    log.info("Weekly report scheduled on %s at %02d:%02d UTC",
             days[day_of_week % 7], hour, minute)


def get_next_run() -> Optional[str]:
    """Return ISO-8601 string of the next scheduled run, or None."""
    sched = _get_scheduler()
    job = sched.get_job(JOB_ID)
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None
