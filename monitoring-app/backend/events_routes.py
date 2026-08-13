"""
events_routes.py — Audit log API for HyperMonitor.

Endpoints
─────────
GET  /api/events          – paginated event list with filters
GET  /api/events/stats    – summary counts by category + severity
DELETE /api/events        – purge events older than N days (events_write)

Helper (used by other modules)
───────────────────────────────
  from events_routes import log_event
  await log_event(db, actor="root", category="auth", action="login",
                  target="", detail="Login from 1.2.3.4", severity="info",
                  actor_ip="1.2.3.4")
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import and_, delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import SessionLocal
import models
import auth as _auth

router = APIRouter(prefix="/api/events", tags=["Events"])


# ── DB dependency ──────────────────────────────────────────────────────────────

async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class EventResponse(BaseModel):
    id:        int
    ts:        str          # ISO 8601 UTC string
    actor:     str
    actor_ip:  str
    category:  str
    action:    str
    target:    str
    detail:    str
    severity:  str


class EventsPage(BaseModel):
    total:   int
    page:    int
    pages:   int
    items:   List[EventResponse]


class EventStats(BaseModel):
    total:       int
    by_category: dict
    by_severity: dict
    by_actor:    List[dict]   # top-10 actors


class PurgeResponse(BaseModel):
    deleted: int
    detail:  str


# ── Helper — called by other route modules ────────────────────────────────────

async def log_event(
    db:       AsyncSession,
    actor:    str,
    category: str,
    action:   str,
    target:   str  = "",
    detail:   str  = "",
    severity: str  = "info",
    actor_ip: str  = "",
) -> None:
    """
    Append one audit-log row.  Never raises — a logging failure must never
    break the actual operation being logged.
    """
    try:
        row = models.AuditLog(
            ts       = datetime.utcnow().replace(tzinfo=None),
            actor    = actor,
            actor_ip = actor_ip,
            category = category,
            action   = action,
            target   = target   or "",
            detail   = detail   or "",
            severity = severity,
        )
        db.add(row)
        await db.flush()   # write to transaction; caller still controls commit
    except Exception:
        pass   # never propagate — logging must be best-effort


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=EventsPage,
            dependencies=[Depends(_auth.require_perm("events_view"))])
async def list_events(
    db:        AsyncSession = Depends(get_db),
    page:      int          = Query(1,    ge=1,   description="Page number (1-based)"),
    page_size: int          = Query(50,   ge=1,   le=200, description="Rows per page"),
    category:  Optional[str] = Query(None, description="Filter by category"),
    severity:  Optional[str] = Query(None, description="Filter by severity"),
    actor:     Optional[str] = Query(None, description="Filter by actor username"),
    action:    Optional[str] = Query(None, description="Substring match on action"),
    search:    Optional[str] = Query(None, description="Full-text substring on detail/target"),
    since:     Optional[str] = Query(None, description="ISO date lower bound, e.g. 2024-01-01"),
    until:     Optional[str] = Query(None, description="ISO date upper bound, e.g. 2024-12-31"),
):
    """
    Return a paginated, filtered list of audit-log entries.
    Newest entries first.
    """
    filters = []
    if category: filters.append(models.AuditLog.category == category)
    if severity: filters.append(models.AuditLog.severity == severity)
    if actor:    filters.append(models.AuditLog.actor    == actor)
    if action:   filters.append(models.AuditLog.action.ilike(f"%{action}%"))
    if search:
        filters.append(
            models.AuditLog.detail.ilike(f"%{search}%") |
            models.AuditLog.target.ilike(f"%{search}%")
        )
    if since:
        try:
            filters.append(models.AuditLog.ts >= datetime.fromisoformat(since))
        except ValueError:
            pass
    if until:
        try:
            # Include the whole "until" day
            until_dt = datetime.fromisoformat(until) + timedelta(days=1)
            filters.append(models.AuditLog.ts < until_dt)
        except ValueError:
            pass

    where = and_(*filters) if filters else True

    # Total count
    count_q  = select(func.count()).select_from(models.AuditLog).where(where)
    total    = (await db.execute(count_q)).scalar_one()

    # Paginated rows
    offset   = (page - 1) * page_size
    rows_q   = (
        select(models.AuditLog)
        .where(where)
        .order_by(desc(models.AuditLog.ts), desc(models.AuditLog.id))
        .offset(offset)
        .limit(page_size)
    )
    rows = (await db.execute(rows_q)).scalars().all()

    pages = max(1, (total + page_size - 1) // page_size)

    return EventsPage(
        total=total,
        page=page,
        pages=pages,
        items=[
            EventResponse(
                id       = r.id,
                ts       = r.ts.isoformat() if r.ts else "",
                actor    = r.actor    or "",
                actor_ip = r.actor_ip or "",
                category = r.category or "",
                action   = r.action   or "",
                target   = r.target   or "",
                detail   = r.detail   or "",
                severity = r.severity or "info",
            )
            for r in rows
        ],
    )


@router.get("/stats", response_model=EventStats,
            dependencies=[Depends(_auth.require_perm("events_view"))])
async def event_stats(db: AsyncSession = Depends(get_db)):
    """Return aggregate counts used by the summary cards at the top of the tab."""
    total = (await db.execute(
        select(func.count()).select_from(models.AuditLog)
    )).scalar_one()

    # By category
    cat_rows = (await db.execute(
        select(models.AuditLog.category, func.count())
        .group_by(models.AuditLog.category)
    )).all()
    by_category = {r[0]: r[1] for r in cat_rows}

    # By severity
    sev_rows = (await db.execute(
        select(models.AuditLog.severity, func.count())
        .group_by(models.AuditLog.severity)
    )).all()
    by_severity = {r[0]: r[1] for r in sev_rows}

    # Top-10 actors
    actor_rows = (await db.execute(
        select(models.AuditLog.actor, func.count().label("cnt"))
        .group_by(models.AuditLog.actor)
        .order_by(desc("cnt"))
        .limit(10)
    )).all()
    by_actor = [{"actor": r[0], "count": r[1]} for r in actor_rows]

    return EventStats(
        total=total,
        by_category=by_category,
        by_severity=by_severity,
        by_actor=by_actor,
    )


@router.delete("", response_model=PurgeResponse,
               dependencies=[Depends(_auth.require_perm("events_write"))])
async def purge_events(
    older_than_days: int = Query(90, ge=1, description="Delete events older than N days"),
    db: AsyncSession = Depends(get_db),
    current_user: models.AuthUser = Depends(_auth.get_current_user),
):
    """
    Delete audit-log entries older than `older_than_days` days.
    Requires events_write permission.  The purge itself is also logged.
    """
    cutoff = datetime.utcnow().replace(tzinfo=None) - timedelta(days=older_than_days)
    result = await db.execute(
        delete(models.AuditLog).where(models.AuditLog.ts < cutoff)
    )
    deleted = result.rowcount
    # Log the purge action
    await log_event(
        db, actor=current_user.username, category="system",
        action="events.purge",
        detail=f"Purged {deleted} log entries older than {older_than_days} days (cutoff: {cutoff.date()})",
        severity="warning",
    )
    await db.commit()
    return PurgeResponse(
        deleted=deleted,
        detail=f"Deleted {deleted} log entr{'y' if deleted == 1 else 'ies'} older than {older_than_days} days.",
    )
