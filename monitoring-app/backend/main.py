"""
main.py — FastAPI backend v4.0 — HyperMonitor

API surface
───────────
Server management:
  POST   /api/servers/config              – add a hypervisor host
  GET    /api/servers/config              – list all configured hosts
  PUT    /api/servers/config/{id}         – update a host
  DELETE /api/servers/config/{id}         – remove a host
  POST   /api/servers/probe/{id}          – detect hardware specs (RAM/CPU/Disk)
  PATCH  /api/servers/config/{id}/toggle  – flip the enabled flag

VM metadata:
  GET    /api/vms/metadata                – all static VM records
  PUT    /api/vms/metadata/{vm_id}        – create or update a VM metadata record

Live monitoring (served from 30 s cache — no blocking on hypervisors):
  GET    /api/servers                     – live host metrics (includes cache_age_s)
  GET    /api/vms                         – VM inventory
  GET    /api/vms/{vm_id}                 – single VM detail
  GET    /api/hypervisors                 – aggregated stats per hypervisor type

Cache control:
  POST   /api/cache/refresh               – force immediate re-poll (all or one server)

CSV downloads:
  GET    /api/reports/servers.csv         – dashboard summary CSV
  GET    /api/reports/vms.csv             – full VM inventory CSV
  GET    /api/reports/vms/{server_id}.csv – per-server VM inventory CSV

Email reports:
  GET    /api/email/config                – SMTP settings
  PUT    /api/email/config                – save SMTP settings
  POST   /api/email/test                  – send test email
  POST   /api/email/send-report           – send full report now

Scheduled reports:
  GET    /api/email/schedule              – current schedule
  PUT    /api/email/schedule              – create / update schedule
  DELETE /api/email/schedule              – disable schedule

Utility:
  GET    /health                          – liveness probe
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import time
import uuid
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from crypto import decrypt, encrypt
from database import SessionLocal, engine
import cache as metrics_cache
import models
import scheduler as report_scheduler
import auth as _auth
from auth_routes import router as auth_router
from users_routes import router as users_router
from events_routes import router as events_router
from events_routes import log_event
from hypervisors import get_adapter, REGISTRY
from query_builder import VMQueryBuilder

# ──────────────────────────────────────────────────────────────────────────────
# App bootstrap
# ──────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Multi-Hypervisor Server Monitor",
    description="Live CPU / RAM / Storage / VM monitoring — all data from real hypervisor APIs.",
    version="4.0.0",
)

# ── Register auth + user-management + events routers ─────────────────────────
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(events_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────────────────────────────────────
# Startup — create tables
# ──────────────────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup_event():
    async with engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.create_all)

    # ── Seed built-in groups + root user (idempotent) ────────────────────────
    await _auth.ensure_defaults()

    # ── Start background metrics poller ─────────────────────────────────────
    async def _get_enabled_rows():
        async with SessionLocal() as db:
            result = await db.execute(
                select(models.ServerConfig).where(models.ServerConfig.enabled == True))
            return result.scalars().all()

    metrics_cache.start_background_poller(_fetch_live_metrics, _get_enabled_rows)

    # ── Restore scheduled report if one was saved ────────────────────────────
    async with SessionLocal() as db:
        result = await db.execute(
            select(models.EmailSchedule).where(models.EmailSchedule.id == 1))
        sched_row = result.scalars().first()

    report_scheduler.start()
    if sched_row and sched_row.enabled and sched_row.schedule_type != "disabled":
        _register_schedule(sched_row)


@app.on_event("shutdown")
async def shutdown_event():
    metrics_cache.stop_background_poller()
    report_scheduler.stop()


# ──────────────────────────────────────────────────────────────────────────────
# DB session
# ──────────────────────────────────────────────────────────────────────────────

async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


# ──────────────────────────────────────────────────────────────────────────────
# Pydantic schemas
# ──────────────────────────────────────────────────────────────────────────────

# Derive valid hypervisor types from the registry — add a new adapter and this
# updates automatically with zero extra changes.
VALID_HV_TYPES = set(REGISTRY.keys())


class ServerConfigCreate(BaseModel):
    display_name:     str
    ip_address:       str
    hostname:         Optional[str] = ""
    hypervisor_type:  str
    username:         Optional[str] = ""
    password:         Optional[str] = ""
    ram_total_gb:     float = 0.0
    storage_total_tb: float = 0.0
    enabled:          bool  = True

    @field_validator("hypervisor_type")
    @classmethod
    def check_hv(cls, v: str) -> str:
        if v not in VALID_HV_TYPES:
            raise ValueError(f"hypervisor_type must be one of {VALID_HV_TYPES}")
        return v


class ServerConfigResponse(BaseModel):
    server_id:        str
    display_name:     str
    ip_address:       str
    hostname:         str
    hypervisor_type:  str
    ram_total_gb:     float
    storage_total_tb: float
    cpu_cores:        int
    enabled:          bool
    has_credentials:  bool
    probe_status:     str


class ProbeResult(BaseModel):
    server_id:        str
    ram_total_gb:     float
    storage_total_tb: float
    cpu_cores:        int
    probe_status:     str
    message:          str


class DriveInfo(BaseModel):
    """One storage volume / drive on a host."""
    name:       str    # e.g. "C:", "/dev/sda", "datastore1"
    total_gb:   float
    used_gb:    float
    free_gb:    float
    usage_pct:  float


class ServerMetrics(BaseModel):
    server_id:          str
    hostname:           str
    display_name:       str
    hypervisor_type:    str
    ip_address:         str
    cpu_usage_pct:      float
    cpu_cores:          int
    ram_used_gb:        float
    ram_total_gb:       float
    ram_usage_pct:      float
    storage_used_tb:    float
    storage_total_tb:   float
    storage_usage_pct:  float
    drives:             List[DriveInfo]   # per-volume breakdown
    vm_count:           int
    status:             str
    cache_age_s:        Optional[float] = None  # seconds since last successful poll
    error:              Optional[str]   = None  # connection error message if any


class VMRecord(BaseModel):
    vm_id:           str
    vm_name:         str
    ip_address:      str
    hypervisor_type: str
    host_server_id:  str            # which host this VM belongs to
    cpu_usage_pct:   float
    cpu_cores:       int
    ram_used_gb:     float
    ram_total_gb:    float
    ram_usage_pct:   float
    power_state:     str            # "running" | "stopped" | "paused" | "unknown"
    owner_name:      str
    creation_date:   str
    purpose:         str
    status:          str
    snapshot_count:  int = 0


class HypervisorSummary(BaseModel):
    name:         str
    server_count: int
    vm_count:     int
    avg_cpu_pct:  float
    avg_ram_pct:  float


class VMMetadataCreate(BaseModel):
    """Payload for creating / updating static VM metadata."""
    vm_id:        str
    vm_name:      str
    ip_address:   str = ""
    hypervisor_type: str = ""
    owner_name:   str = ""
    creation_date: str = ""   # ISO 8601, e.g. "2024-01-15"
    purpose:      str = ""


class VMMetadataResponse(BaseModel):
    vm_id:          str
    vm_name:        str
    ip_address:     str
    hypervisor_type: str
    owner_name:     str
    creation_date:  str
    purpose:        str


class EmailConfigPayload(BaseModel):
    """Payload for saving SMTP / report settings."""
    smtp_host:    str
    smtp_port:    int   = 587
    smtp_user:    str   = ""
    smtp_password: str  = ""   # plain-text from UI; stored encrypted
    use_tls:      bool  = False
    # smtp_mode: "smtps" | "starttls" | "plain"
    # "plain" = port 25, no TLS, no STARTTLS — internal corporate relay
    smtp_mode:    str   = "starttls"
    from_address: str   = ""
    recipients:   str   = ""   # comma-separated email addresses


class EmailConfigResponse(BaseModel):
    smtp_host:       str
    smtp_port:       int
    smtp_user:       str
    use_tls:         bool
    smtp_mode:       str
    from_address:    str
    recipients:      str
    has_password:    bool


class VMMetadataBulkItem(BaseModel):
    """One VM record in the bulk-upsert payload."""
    vm_id:        str
    vm_name:      str
    ip_address:   str = ""
    hypervisor_type: str = ""
    owner_name:   str = ""
    creation_date: str = ""
    purpose:      str = ""


class SendReportRequest(BaseModel):
    """Optional override — leave blank to use saved config."""
    server_id:     Optional[str] = None   # None = all enabled servers
    report_format: str           = "both" # "html" | "csv" | "both"


# ── Req 3 — Snapshot + Event Pydantic schemas ─────────────────────────────────

class SnapshotResponse(BaseModel):
    """Normalized snapshot record returned by the API."""
    id:              int
    server_id:       str
    vm_id:           str
    vm_name:         str
    snap_name:       str
    created_at:      Optional[str]   # ISO-8601 UTC or None
    size_bytes:      int             # 0 when the hypervisor doesn't expose disk size
    hypervisor_type: str
    extra:           Dict[str, Any]  # hypervisor-specific extras (from JSONB/JSON)
    fetched_at:      str             # ISO-8601 UTC


class VMEventResponse(BaseModel):
    """Normalized VM/hypervisor event record."""
    id:              int
    server_id:       str
    vm_id:           str
    vm_name:         str
    event_type:      str
    severity:        str
    message:         str
    occurred_at:     Optional[str]   # ISO-8601 UTC or None
    hypervisor_type: str
    metadata:        Dict[str, Any]  # extensible KV extras
    ingested_at:     str             # ISO-8601 UTC


class IngestSnapshotsRequest(BaseModel):
    """
    Trigger on-demand snapshot ingestion for a VM.
    Calls the registered adapter's get_vm_snapshots() and persists the result.
    """
    server_id: str
    vm_name:   str


class IngestEventRequest(BaseModel):
    """
    Ingest a single normalized event — useful for testing and external integrations.
    To ingest hypervisor-native events in bulk, use the background collector.
    """
    server_id:       str
    vm_id:           str = ""
    vm_name:         str = ""
    event_type:      str
    severity:        str = "info"
    message:         str
    occurred_at:     Optional[str] = None  # ISO-8601; defaults to now
    hypervisor_type: str
    metadata:        Dict[str, Any] = {}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_server_id(display_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", display_name.lower()).strip("-")[:30]
    return f"{slug}-{uuid.uuid4().hex[:6]}"


def _status_from_cpu(cpu: float) -> str:
    if cpu >= 90:   return "critical"
    if cpu >= 70:   return "warning"
    return "online"


def _row_to_response(row: models.ServerConfig) -> ServerConfigResponse:
    return ServerConfigResponse(
        server_id=row.server_id,
        display_name=row.display_name,
        ip_address=row.ip_address,
        hostname=row.hostname or "",
        hypervisor_type=row.hypervisor_type,
        ram_total_gb=float(row.ram_total_gb or 0),
        storage_total_tb=float(row.storage_total_tb or 0),
        cpu_cores=int(row.cpu_cores or 0),
        enabled=row.enabled,
        has_credentials=bool(row.username_enc and row.password_enc),
        probe_status=row.probe_status or "pending",
    )


PURPOSES = [
    "Web Server", "Database Server", "CI/CD Runner", "Load Balancer",
    "Mail Server", "Monitoring Node", "Dev Sandbox",  "Staging Env",
    "Analytics",  "Backup Agent",    "API Gateway",   "Log Aggregator",
    "Build Node",  "Test Runner",     "Cache Node",    "File Server",
]
OWNERS = ["alice", "bob", "carol", "dave", "eve", "frank",
          "grace", "henry", "irene", "jack", "kate", "leo"]


# ──────────────────────────────────────────────────────────────────────────────
# Dispatcher — Strategy Pattern via the hypervisor registry
# ──────────────────────────────────────────────────────────────────────────────

def _fetch_live_metrics(row: models.ServerConfig) -> dict:
    """
    Blocking: called in a ThreadPoolExecutor.

    Delegates entirely to the registered HypervisorInterface adapter.
    Adding a new hypervisor requires NO changes here — only a new adapter
    class + one line in hypervisors/REGISTRY.
    """
    username = decrypt(row.username_enc)
    password = decrypt(row.password_enc)

    if not username or not password:
        # Build a safe fallback without instantiating the adapter
        adapter_cls = REGISTRY.get(row.hypervisor_type)
        from hypervisors.base import HypervisorInterface
        # Use the base _fallback helper via a lightweight proxy
        proxy = object.__new__(HypervisorInterface)  # type: ignore[abstract]
        proxy.row      = row                          # type: ignore[attr-defined]
        proxy.username = username                     # type: ignore[attr-defined]
        proxy.password = password                     # type: ignore[attr-defined]
        proxy.ip       = row.ip_address              # type: ignore[attr-defined]
        fb = proxy._fallback("No credentials — add via Manage Servers.")
        return fb

    try:
        adapter = get_adapter(row, username, password)
        return adapter.get_server_status()
    except ValueError as exc:
        # Unknown hypervisor_type — get_adapter raises ValueError
        from hypervisors.base import HypervisorInterface
        proxy = object.__new__(HypervisorInterface)  # type: ignore[abstract]
        proxy.row      = row                          # type: ignore[attr-defined]
        proxy.username = username                     # type: ignore[attr-defined]
        proxy.password = password                     # type: ignore[attr-defined]
        proxy.ip       = row.ip_address              # type: ignore[attr-defined]
        return proxy._fallback(str(exc))


# ──────────────────────────────────────────────────────────────────────────────
# Routes — Server Configuration CRUD
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/api/servers/config", response_model=ServerConfigResponse,
          status_code=201, tags=["Server Management"],
          dependencies=[Depends(_auth.require_perm("servers_write"))])
async def add_server(payload: ServerConfigCreate,
                     db: AsyncSession = Depends(get_db),
                     current_user: models.AuthUser = Depends(_auth.get_current_user)):
    server_id = _make_server_id(payload.display_name)
    row = models.ServerConfig(
        server_id=server_id,
        display_name=payload.display_name,
        ip_address=payload.ip_address,
        hostname=payload.hostname or "",
        hypervisor_type=payload.hypervisor_type,
        username_enc=encrypt(payload.username or ""),
        password_enc=encrypt(payload.password or ""),
        ram_total_gb="0", storage_total_tb="0",
        cpu_cores="0", probe_status="pending",
        enabled=payload.enabled,
    )
    db.add(row)
    await log_event(db, actor=current_user.username, category="servers",
                    action="server.add", target=server_id,
                    detail=f"Added {payload.hypervisor_type} server '{payload.display_name}' ({payload.ip_address})")
    await db.commit()
    await db.refresh(row)
    return _row_to_response(row)


@app.get("/api/servers/config", response_model=List[ServerConfigResponse],
         tags=["Server Management"],
         dependencies=[Depends(_auth.require_perm("servers_view"))])
async def list_servers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.ServerConfig))
    return [_row_to_response(r) for r in result.scalars().all()]


@app.put("/api/servers/config/{server_id}", response_model=ServerConfigResponse,
         tags=["Server Management"],
         dependencies=[Depends(_auth.require_perm("servers_write"))])
async def update_server(server_id: str, payload: ServerConfigCreate,
                        db: AsyncSession = Depends(get_db),
                        current_user: models.AuthUser = Depends(_auth.get_current_user)):
    result = await db.execute(
        select(models.ServerConfig).where(models.ServerConfig.server_id == server_id))
    row = result.scalars().first()
    if not row:
        raise HTTPException(404, f"Server '{server_id}' not found")

    ip_changed   = payload.ip_address != row.ip_address
    cred_changed = bool(payload.username or payload.password)

    row.display_name    = payload.display_name
    row.ip_address      = payload.ip_address
    row.hostname        = payload.hostname or ""
    row.hypervisor_type = payload.hypervisor_type
    row.enabled         = payload.enabled
    if payload.ram_total_gb     > 0: row.ram_total_gb     = str(payload.ram_total_gb)
    if payload.storage_total_tb > 0: row.storage_total_tb = str(payload.storage_total_tb)
    if payload.username: row.username_enc = encrypt(payload.username)
    if payload.password: row.password_enc = encrypt(payload.password)
    if ip_changed or cred_changed:
        row.probe_status = "pending"

    changes = []
    if ip_changed:    changes.append(f"IP changed to {payload.ip_address}")
    if cred_changed:  changes.append("credentials updated")
    await log_event(db, actor=current_user.username, category="servers",
                    action="server.update", target=server_id,
                    detail=f"Updated '{payload.display_name}'" + (f": {', '.join(changes)}" if changes else ""))
    await db.commit()
    await db.refresh(row)
    return _row_to_response(row)


@app.delete("/api/servers/config/{server_id}", status_code=204,
            tags=["Server Management"],
            dependencies=[Depends(_auth.require_perm("servers_write"))])
async def delete_server(server_id: str, db: AsyncSession = Depends(get_db),
                        current_user: models.AuthUser = Depends(_auth.get_current_user)):
    result = await db.execute(
        select(models.ServerConfig).where(models.ServerConfig.server_id == server_id))
    row = result.scalars().first()
    if not row:
        raise HTTPException(404, f"Server '{server_id}' not found")
    name = row.display_name
    await db.delete(row)
    await log_event(db, actor=current_user.username, category="servers",
                    action="server.delete", target=server_id,
                    detail=f"Deleted server '{name}'", severity="warning")
    await db.commit()


@app.patch("/api/servers/config/{server_id}/toggle",
           response_model=ServerConfigResponse, tags=["Server Management"],
           dependencies=[Depends(_auth.require_perm("servers_write"))])
async def toggle_server(server_id: str, db: AsyncSession = Depends(get_db),
                        current_user: models.AuthUser = Depends(_auth.get_current_user)):
    """Flip the enabled flag without touching any other field."""
    result = await db.execute(
        select(models.ServerConfig).where(models.ServerConfig.server_id == server_id))
    row = result.scalars().first()
    if not row:
        raise HTTPException(404, f"Server '{server_id}' not found")
    row.enabled = not row.enabled
    await log_event(db, actor=current_user.username, category="servers",
                    action="server.toggle", target=server_id,
                    detail=f"Server '{row.display_name}' {'enabled' if row.enabled else 'disabled'}")
    await db.commit()
    await db.refresh(row)
    return _row_to_response(row)


# ──────────────────────────────────────────────────────────────────────────────
# Probe endpoint — detect hardware specs and persist
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/api/servers/probe/{server_id}", response_model=ProbeResult,
          tags=["Server Management"],
          dependencies=[Depends(_auth.require_perm("servers_write"))])
async def probe_server(server_id: str, db: AsyncSession = Depends(get_db)):
    """
    Connect to the host, read hardware specs (RAM/CPU/Disk), and store them.
    Reuses the same live adapters as /api/servers — the full metrics dict
    contains cpu_cores, ram_total_gb, storage_total_tb from real data.
    """
    import asyncio

    result = await db.execute(
        select(models.ServerConfig).where(models.ServerConfig.server_id == server_id))
    row = result.scalars().first()
    if not row:
        raise HTTPException(404, f"Server '{server_id}' not found")

    username = decrypt(row.username_enc)
    password = decrypt(row.password_enc)

    if not username or not password:
        row.probe_status = "failed"
        await db.commit()
        return ProbeResult(server_id=server_id, ram_total_gb=0, storage_total_tb=0,
                           cpu_cores=0, probe_status="failed",
                           message="No credentials stored.")

    loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        metrics = await loop.run_in_executor(pool, _fetch_live_metrics, row)

    if metrics.get("error"):
        row.probe_status = "failed"
        await db.commit()
        return ProbeResult(server_id=server_id,
                           ram_total_gb=0, storage_total_tb=0, cpu_cores=0,
                           probe_status="failed", message=metrics["error"])

    row.ram_total_gb     = str(metrics["ram_total_gb"])
    row.storage_total_tb = str(metrics["storage_total_tb"])
    row.cpu_cores        = str(metrics["cpu_cores"])
    row.probe_status     = "ok"
    await db.commit()

    return ProbeResult(
        server_id=server_id,
        ram_total_gb=metrics["ram_total_gb"],
        storage_total_tb=metrics["storage_total_tb"],
        cpu_cores=metrics["cpu_cores"],
        probe_status="ok",
        message=(f"Detected: {metrics['cpu_cores']} CPUs, "
                 f"{metrics['ram_total_gb']} GB RAM, "
                 f"{metrics['storage_total_tb']} TB disk"),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Routes — Live Monitoring (served from in-memory cache)
# ──────────────────────────────────────────────────────────────────────────────

def _metrics_to_server_model(m: dict) -> ServerMetrics:
    return ServerMetrics(
        server_id=m["server_id"],
        hostname=m["hostname"],
        display_name=m["display_name"],
        hypervisor_type=m["hypervisor_type"],
        ip_address=m["ip_address"],
        cpu_usage_pct=m["cpu_usage_pct"],
        cpu_cores=m["cpu_cores"],
        ram_used_gb=m["ram_used_gb"],
        ram_total_gb=m["ram_total_gb"],
        ram_usage_pct=m["ram_usage_pct"],
        storage_used_tb=m["storage_used_tb"],
        storage_total_tb=m["storage_total_tb"],
        storage_usage_pct=m["storage_usage_pct"],
        drives=[DriveInfo(**d) for d in m.get("drives", [])],
        vm_count=m["vm_count"],
        status=m["status"],
        # Expose cache staleness so the UI can warn when data is old
        cache_age_s=round(metrics_cache.cache_age(m["server_id"]) or 0, 1),
        error=m.get("error") or None,
    )


@app.get("/api/servers", response_model=List[ServerMetrics], tags=["Monitoring"],
         dependencies=[Depends(_auth.require_perm("dashboard_view"))])
async def get_servers(db: AsyncSession = Depends(get_db)):
    """
    Return host metrics using stale-while-revalidate:
    1. Immediately return any cached data (fresh or stale) — no blank screen.
    2. For entries with no cache at all (brand-new server), fetch live and block.
    3. Stale entries are refreshed in the background by the 60 s poller.
    """
    import asyncio

    result = await db.execute(
        select(models.ServerConfig).where(models.ServerConfig.enabled == True))
    rows = result.scalars().all()
    if not rows:
        return []

    out: List[ServerMetrics] = []
    truly_cold = []   # no cache entry at all — must fetch synchronously

    for row in rows:
        # Fresh first, then stale, then cold
        data = metrics_cache.get_cached(row.server_id) \
               or metrics_cache.get_cached_stale(row.server_id)
        if data:
            out.append(_metrics_to_server_model(data))
        else:
            truly_cold.append(row)

    if truly_cold:
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(truly_cold)) as pool:
            futures = [loop.run_in_executor(pool, _fetch_live_metrics, r) for r in truly_cold]
            for m in await asyncio.gather(*futures):
                metrics_cache.set_cached(m["server_id"], m)
                out.append(_metrics_to_server_model(m))

    return out


@app.get("/api/vms", response_model=List[VMRecord], tags=["Monitoring"],
         dependencies=[Depends(_auth.require_perm("dashboard_view"))])
async def get_vms(
    db:              AsyncSession = Depends(get_db),
    hypervisor_type: Optional[str] = Query(None,
        description="Filter by hypervisor type, e.g. 'VMware ESXi'"),
    server_id:       Optional[str] = Query(None,
        description="Filter to VMs on a specific host (server_id slug)"),
    power_state:     Optional[str] = Query(None,
        description="Filter by power state: running | stopped | paused"),
    status:          Optional[str] = Query(None,
        description="Filter by status: online | warning | critical | stopped"),
    search:          Optional[str] = Query(None,
        description="Smart global search: IP prefix, server slug, or free-text "
                    "(matches vm_name, owner_name, purpose, ip_address via OR)"),
):
    """
    Return VM inventory from the in-memory cache (same 30 s TTL as /api/servers).
    Static metadata (owner, purpose) is merged from PostgreSQL.

    Supports composable query parameters — all are optional, all additive:
      ?hypervisor_type=VMware+ESXi
      ?server_id=prod-esxi-abc123
      ?power_state=running
      ?status=critical
      ?search=192.168.1          ← IP prefix auto-detected
      ?search=alice              ← free-text owner/name match
      ?search=prod-kvm-7f2a1b   ← slug → exact host_server_id match

    All filters merge via logical AND using the VMQueryBuilder.
    No nested if/else statements — add a new dimension by calling
    builder.filter_*() without touching any other route logic.
    """
    import asyncio

    result = await db.execute(
        select(models.ServerConfig).where(models.ServerConfig.enabled == True))
    rows = result.scalars().all()
    if not rows:
        return []

    # Stale-while-revalidate: return any cached data immediately
    all_metrics = []
    truly_cold  = []
    for row in rows:
        data = metrics_cache.get_cached(row.server_id) \
               or metrics_cache.get_cached_stale(row.server_id)
        if data:
            all_metrics.append(data)
        else:
            truly_cold.append(row)

    if truly_cold:
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(truly_cold)) as pool:
            futures = [loop.run_in_executor(pool, _fetch_live_metrics, r) for r in truly_cold]
            for m in await asyncio.gather(*futures):
                metrics_cache.set_cached(m["server_id"], m)
                all_metrics.append(m)

    # Load all static VM metadata in one query (for owner/purpose enrichment)
    meta_result = await db.execute(select(models.VMMetadata))
    static_map: Dict[str, models.VMMetadata] = {
        m.vm_name: m for m in meta_result.scalars().all()
    }

    # Load snapshot counts grouped by vm_id in one query
    from sqlalchemy import func as sa_func
    snap_result = await db.execute(
        select(models.VMSnapshot.vm_id, sa_func.count().label("cnt"))
        .group_by(models.VMSnapshot.vm_id)
    )
    snapshot_counts: Dict[str, int] = {row.vm_id: row.cnt for row in snap_result.all()}

    out: List[VMRecord] = []
    today = date.today()
    vm_idx = 0

    for m in all_metrics:
        host_server_id = m["server_id"]
        hv_type        = m["hypervisor_type"]

        for vm in m.get("vms", []):
            vm_name    = vm["vm_name"]
            power      = vm["power_state"]
            cpu_pct    = float(vm.get("cpu_pct",    0))
            cpu_cores  = int(vm.get("cpu_cores",    1))
            ram_total  = float(vm.get("ram_total_gb", 0))
            ram_used   = float(vm.get("ram_used_gb",  0))
            ram_pct    = round(ram_used / max(ram_total, 0.1) * 100, 1)
            vm_ip      = vm.get("ip_address", "")

            # Enrich with static metadata if available
            meta = static_map.get(vm_name)
            owner   = meta.owner_name    if meta else OWNERS[vm_idx % len(OWNERS)]
            purpose = meta.purpose       if meta else PURPOSES[vm_idx % len(PURPOSES)]
            created = (meta.creation_date.isoformat()
                       if meta else (today - timedelta(days=vm_idx * 30)).isoformat())

            # Derive a stable vm_id from host + vm name.
            # Include host_server_id prefix to prevent cross-host collisions when
            # two hypervisors have VMs with identical names.
            short     = hv_type.replace(" ", "").replace("-", "")[:4].lower()
            safe_name = re.sub(r"[^a-z0-9]", "-", vm_name.lower())[:20]
            # Use last 6 chars of server_id as disambiguator
            srv_suffix = host_server_id[-6:] if len(host_server_id) >= 6 else host_server_id
            vm_id     = f"{short}-{srv_suffix}-{safe_name}"

            # Determine status from power state + CPU
            if power == "stopped":
                _status = "stopped"
            elif cpu_pct >= 90:
                _status = "critical"
            elif cpu_pct >= 70:
                _status = "warning"
            else:
                _status = "online"

            out.append(VMRecord(
                vm_id=vm_id,
                vm_name=vm_name,
                ip_address=vm_ip,
                hypervisor_type=hv_type,
                host_server_id=host_server_id,
                cpu_usage_pct=cpu_pct,
                cpu_cores=cpu_cores,
                ram_used_gb=ram_used,
                ram_total_gb=ram_total,
                ram_usage_pct=ram_pct,
                power_state=power,
                owner_name=owner,
                creation_date=created,
                purpose=purpose,
                status=_status,
                snapshot_count=snapshot_counts.get(vm_id, 0),
            ))
            vm_idx += 1

    # ── Apply Builder Pattern filters (Req 2 & 4) ────────────────────────────
    # Convert VMRecord objects to plain dicts for the builder, then back.
    # The builder works on dicts so it has no dependency on Pydantic models.
    raw_dicts = [v.model_dump() for v in out]
    filtered  = (
        VMQueryBuilder(raw_dicts)
        .filter_hypervisor(hypervisor_type)
        .filter_server(server_id)
        .filter_power_state(power_state)
        .filter_status(status)
        .search(search)
        .build()
    )
    return [VMRecord(**d) for d in filtered]


# ──────────────────────────────────────────────────────────────────────────────
# VM Metadata CRUD — static fields editable via the UI
#
# NOTE: These routes MUST appear before /api/vms/{vm_id}.
# FastAPI matches routes in registration order; if {vm_id} came first it would
# greedily capture the literal string "metadata" as a path parameter.
# ──────────────────────────────────────────────────────────────────────────────

def _meta_to_response(m: models.VMMetadata) -> VMMetadataResponse:
    return VMMetadataResponse(
        vm_id=m.vm_id,
        vm_name=m.vm_name,
        ip_address=m.ip_address or "",
        hypervisor_type=m.hypervisor_type or "",
        owner_name=m.owner_name or "",
        creation_date=m.creation_date.isoformat() if m.creation_date else "",
        purpose=m.purpose or "",
    )


@app.get("/api/vms/metadata", response_model=List[VMMetadataResponse],
         tags=["VM Metadata"],
         dependencies=[Depends(_auth.require_perm("dashboard_view"))])
async def list_vm_metadata(db: AsyncSession = Depends(get_db)):
    """Return all static VM metadata records stored in PostgreSQL."""
    result = await db.execute(select(models.VMMetadata))
    return [_meta_to_response(m) for m in result.scalars().all()]


@app.put("/api/vms/metadata/{vm_id}", response_model=VMMetadataResponse,
         tags=["VM Metadata"],
         dependencies=[Depends(_auth.require_perm("dashboard_write"))])
async def upsert_vm_metadata(vm_id: str, payload: VMMetadataCreate,
                              db: AsyncSession = Depends(get_db),
                              current_user: models.AuthUser = Depends(_auth.get_current_user)):
    """
    Create or update a static metadata record for a VM.
    Fields: owner_name, creation_date (ISO 8601 string), purpose.
    vm_name, ip_address, hypervisor_type are auto-populated from the live
    inventory if not supplied.
    """
    result = await db.execute(
        select(models.VMMetadata).where(models.VMMetadata.vm_id == vm_id))
    row = result.scalars().first()

    # Parse creation_date — fall back to today
    try:
        cd = date.fromisoformat(payload.creation_date) if payload.creation_date else date.today()
    except ValueError:
        cd = date.today()

    if row:
        # Update existing record
        if payload.vm_name:         row.vm_name         = payload.vm_name
        if payload.ip_address:      row.ip_address      = payload.ip_address
        if payload.hypervisor_type: row.hypervisor_type = payload.hypervisor_type
        if payload.owner_name:      row.owner_name      = payload.owner_name
        row.creation_date = cd
        if payload.purpose is not None: row.purpose = payload.purpose
    else:
        # Create new record
        row = models.VMMetadata(
            vm_id=vm_id,
            vm_name=payload.vm_name or vm_id,
            ip_address=payload.ip_address or "",
            hypervisor_type=payload.hypervisor_type or "",
            owner_name=payload.owner_name or "",
            creation_date=cd,
            purpose=payload.purpose or "",
        )
        db.add(row)

    await log_event(db, actor=current_user.username, category="vms",
                    action="vm.metadata.save", target=vm_id,
                    detail=f"Saved metadata for VM '{payload.vm_name or vm_id}' — owner: {payload.owner_name}, purpose: {payload.purpose}")
    await db.commit()
    await db.refresh(row)
    return _meta_to_response(row)


@app.delete("/api/vms/metadata/{vm_id}", status_code=204, tags=["VM Metadata"],
            dependencies=[Depends(_auth.require_perm("dashboard_write"))])
async def delete_vm_metadata(vm_id: str, db: AsyncSession = Depends(get_db),
                              current_user: models.AuthUser = Depends(_auth.get_current_user)):
    """Delete a static metadata record (the live VM entry is unaffected)."""
    result = await db.execute(
        select(models.VMMetadata).where(models.VMMetadata.vm_id == vm_id))
    row = result.scalars().first()
    if not row:
        raise HTTPException(404, f"VM metadata '{vm_id}' not found")
    await db.delete(row)
    await log_event(db, actor=current_user.username, category="vms",
                    action="vm.metadata.delete", target=vm_id,
                    detail=f"Deleted metadata for VM '{row.vm_name}'", severity="warning")
    await db.commit()


@app.post("/api/vms/metadata/bulk-upsert",
          response_model=List[VMMetadataResponse], tags=["VM Metadata"],
          dependencies=[Depends(_auth.require_perm("dashboard_write"))])
async def bulk_upsert_vm_metadata(
    items: List[VMMetadataBulkItem],
    db: AsyncSession = Depends(get_db),
):
    """
    Create or update multiple VM metadata records in one request.
    Used by the Dashboard inline editor for bulk owner/purpose saves.
    Each item is identified by vm_id; existing records are updated,
    new ones are created.
    """
    if not items:
        return []

    ids = [i.vm_id for i in items]
    result = await db.execute(
        select(models.VMMetadata).where(models.VMMetadata.vm_id.in_(ids)))
    existing: Dict[str, models.VMMetadata] = {
        r.vm_id: r for r in result.scalars().all()
    }

    saved = []
    for item in items:
        try:
            cd = date.fromisoformat(item.creation_date) if item.creation_date else date.today()
        except ValueError:
            cd = date.today()

        if item.vm_id in existing:
            row = existing[item.vm_id]
            if item.vm_name:         row.vm_name         = item.vm_name
            if item.ip_address:      row.ip_address      = item.ip_address
            if item.hypervisor_type: row.hypervisor_type = item.hypervisor_type
            row.owner_name    = item.owner_name    # always overwrite
            row.creation_date = cd
            row.purpose       = item.purpose       # always overwrite
        else:
            row = models.VMMetadata(
                vm_id=item.vm_id,
                vm_name=item.vm_name or item.vm_id,
                ip_address=item.ip_address or "",
                hypervisor_type=item.hypervisor_type or "",
                owner_name=item.owner_name or "",
                creation_date=cd,
                purpose=item.purpose or "",
            )
            db.add(row)
            existing[item.vm_id] = row
        saved.append(row)

    await db.commit()
    for row in saved:
        await db.refresh(row)

    return [_meta_to_response(r) for r in saved]


@app.get("/api/vms/{vm_id}", response_model=VMRecord, tags=["Monitoring"],
         dependencies=[Depends(_auth.require_perm("dashboard_view"))])
async def get_vm(vm_id: str, db: AsyncSession = Depends(get_db)):
    """Return a single VM by its derived ID (short lookup from /api/vms)."""
    all_vms = await get_vms(db=db)
    for vm in all_vms:
        if vm.vm_id == vm_id:
            return vm
    raise HTTPException(404, f"VM '{vm_id}' not found")


@app.get("/api/vms/{vm_id}/snapshots", response_model=List[SnapshotResponse],
         tags=["Snapshots"],
         dependencies=[Depends(_auth.require_perm("dashboard_view"))])
async def get_vm_snapshots_live(
    vm_id:  str,
    db:     AsyncSession = Depends(get_db),
):
    """
    Fetch snapshots for a VM on-demand from the hypervisor and return them.
    Also persists the results to vm_snapshot table for historical reference.
    The vm_id format is '{hv_short}-{srv_suffix}-{safe_name}'.
    The server is located by matching host_server_id across cached metrics.
    """
    import asyncio as _asyncio

    # Locate the VM in cached metrics to find its server and name
    vm_name_found: Optional[str] = None
    srv_row: Optional[models.ServerConfig] = None

    result = await db.execute(
        select(models.ServerConfig).where(models.ServerConfig.enabled == True))
    srv_rows = result.scalars().all()

    for row in srv_rows:
        data = metrics_cache.get_cached(row.server_id) \
               or metrics_cache.get_cached_stale(row.server_id)
        if not data:
            continue
        for vm in data.get("vms", []):
            short      = data["hypervisor_type"].replace(" ", "").replace("-", "")[:4].lower()
            safe_name  = re.sub(r"[^a-z0-9]", "-", vm["vm_name"].lower())[:20]
            srv_suffix = row.server_id[-6:] if len(row.server_id) >= 6 else row.server_id
            candidate  = f"{short}-{srv_suffix}-{safe_name}"
            if candidate == vm_id:
                vm_name_found = vm["vm_name"]
                srv_row       = row
                break
        if vm_name_found:
            break

    if not vm_name_found or not srv_row:
        raise HTTPException(404, f"VM '{vm_id}' not found in live cache.")

    username = decrypt(srv_row.username_enc)
    password = decrypt(srv_row.password_enc)
    if not username or not password:
        raise HTTPException(400, "Server has no credentials — add them via Manage Servers.")

    loop = _asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        from hypervisors import get_adapter
        adapter   = get_adapter(srv_row, username, password)
        raw_snaps = await loop.run_in_executor(pool, adapter.get_vm_snapshots, vm_name_found)

    # Persist to vm_snapshot (replace existing for this vm)
    await db.execute(
        models.VMSnapshot.__table__.delete().where(
            (models.VMSnapshot.server_id == srv_row.server_id) &
            (models.VMSnapshot.vm_name   == vm_name_found)
        )
    )
    from datetime import datetime as _dt
    saved = []
    for snap in raw_snaps:
        created = None
        raw_ts  = snap.get("created_at", "")
        if raw_ts:
            try:
                created = _dt.fromisoformat(raw_ts.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                pass
        s = models.VMSnapshot(
            server_id=srv_row.server_id,
            vm_id=vm_id,
            vm_name=vm_name_found,
            snap_name=snap.get("snap_name", ""),
            created_at=created,
            size_bytes=int(snap.get("size_bytes", 0)),
            hypervisor_type=srv_row.hypervisor_type,
            extra=json.dumps(snap.get("extra", {})),
        )
        db.add(s)
        saved.append(s)
    await db.commit()
    for s in saved:
        await db.refresh(s)
    return [_snap_to_response(s) for s in saved]


@app.get("/api/hypervisors", response_model=List[HypervisorSummary], tags=["Monitoring"],
         dependencies=[Depends(_auth.require_perm("dashboard_view"))])
async def get_hypervisor_summary(db: AsyncSession = Depends(get_db)):
    """
    Aggregate live stats by hypervisor type.
    Served from the in-memory cache (same 30 s TTL as /api/servers).
    Cold servers are fetched on demand and stored in the cache.
    """
    import asyncio

    result = await db.execute(
        select(models.ServerConfig).where(models.ServerConfig.enabled == True))
    rows = result.scalars().all()
    if not rows:
        return []

    # Stale-while-revalidate — serve whatever is in cache immediately
    all_metrics = []
    truly_cold  = []
    for row in rows:
        data = metrics_cache.get_cached(row.server_id) \
               or metrics_cache.get_cached_stale(row.server_id)
        if data:
            all_metrics.append(data)
        else:
            truly_cold.append(row)

    if truly_cold:
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(truly_cold)) as pool:
            futures = [loop.run_in_executor(pool, _fetch_live_metrics, r) for r in truly_cold]
            for m in await asyncio.gather(*futures):
                metrics_cache.set_cached(m["server_id"], m)
                all_metrics.append(m)

    groups: Dict[str, list] = {}
    for m in all_metrics:
        groups.setdefault(m["hypervisor_type"], []).append(m)

    out = []
    for name, hosts in groups.items():
        out.append(HypervisorSummary(
            name=name,
            server_count=len(hosts),
            vm_count=sum(h["vm_count"] for h in hosts),
            avg_cpu_pct=round(sum(h["cpu_usage_pct"] for h in hosts) / len(hosts), 1),
            avg_ram_pct=round(sum(h["ram_usage_pct"] for h in hosts) / len(hosts), 1),
        ))
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Snapshot counts — lightweight dict for the VM inventory badge
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/snapshots/counts", tags=["Snapshots"],
         dependencies=[Depends(_auth.require_perm("dashboard_view"))])
async def get_snapshot_counts(db: AsyncSession = Depends(get_db)):
    """
    Return a mapping of { vm_id: count } for every vm_id that has at least
    one persisted snapshot row.  Used by the Dashboard VM table to eagerly
    populate the Snapshots badge without waiting for the user to expand a panel.
    """
    from sqlalchemy import func as sa_func
    result = await db.execute(
        select(models.VMSnapshot.vm_id, sa_func.count().label("cnt"))
        .group_by(models.VMSnapshot.vm_id)
    )
    return {row.vm_id: row.cnt for row in result.all()}


# ──────────────────────────────────────────────────────────────────────────────
# Cache refresh endpoint — triggers an immediate re-poll outside the 30 s cycle
# ──────────────────────────────────────────────────────────────────────────────

class CacheRefreshRequest(BaseModel):
    """Optional: supply server_id to refresh just one host; omit for all."""
    server_id: Optional[str] = None


@app.post("/api/cache/refresh", tags=["Monitoring"],
          dependencies=[Depends(_auth.require_perm("dashboard_view"))])
async def refresh_cache(
    payload: CacheRefreshRequest = CacheRefreshRequest(),
    db: AsyncSession = Depends(get_db),
):
    """
    Force an immediate live re-poll for one or all enabled servers, bypassing
    the 30 s TTL. The fresh results are stored in the cache so the next
    GET /api/servers call returns them instantly.
    """
    import asyncio

    q = select(models.ServerConfig).where(models.ServerConfig.enabled == True)
    if payload.server_id:
        q = q.where(models.ServerConfig.server_id == payload.server_id)
    rows = (await db.execute(q)).scalars().all()
    if not rows:
        raise HTTPException(404, "No matching enabled servers found.")

    loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(rows), 8)) as pool:
        futures = [loop.run_in_executor(pool, _fetch_live_metrics, r) for r in rows]
        results = await asyncio.gather(*futures)

    for m in results:
        metrics_cache.set_cached(m["server_id"], m)

    return {
        "status":   "ok",
        "refreshed": [m["server_id"] for m in results],
        "count":    len(results),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Email / Report endpoints
# ──────────────────────────────────────────────────────────────────────────────

def _email_row_to_response(row: models.EmailConfig) -> EmailConfigResponse:
    return EmailConfigResponse(
        smtp_host=row.smtp_host or "",
        smtp_port=row.smtp_port or 587,
        smtp_user=row.smtp_user or "",
        use_tls=bool(row.use_tls),
        smtp_mode=row.smtp_mode or "starttls",
        from_address=row.from_address or "",
        recipients=row.recipients or "",
        has_password=bool(row.smtp_password_enc),
    )


@app.get("/api/email/config", response_model=EmailConfigResponse,
         tags=["Email Reports"],
         dependencies=[Depends(_auth.require_perm("email_view"))])
async def get_email_config(db: AsyncSession = Depends(get_db)):
    """Return the current SMTP configuration (password never returned)."""
    result = await db.execute(
        select(models.EmailConfig).where(models.EmailConfig.id == 1))
    row = result.scalars().first()
    if not row:
        # Return safe defaults so the UI form shows empty fields
        return EmailConfigResponse(
            smtp_host="", smtp_port=587, smtp_user="",
            use_tls=False, smtp_mode="starttls",
            from_address="", recipients="", has_password=False,
        )
    return _email_row_to_response(row)


@app.put("/api/email/config", response_model=EmailConfigResponse,
         tags=["Email Reports"],
         dependencies=[Depends(_auth.require_perm("email_write"))])
async def save_email_config(payload: EmailConfigPayload,
                             db: AsyncSession = Depends(get_db),
                             current_user: models.AuthUser = Depends(_auth.get_current_user)):
    """Create or update the SMTP configuration (upsert on id=1)."""
    result = await db.execute(
        select(models.EmailConfig).where(models.EmailConfig.id == 1))
    row = result.scalars().first()
    if row:
        row.smtp_host    = payload.smtp_host
        row.smtp_port    = payload.smtp_port
        row.smtp_user    = payload.smtp_user
        row.use_tls      = payload.use_tls
        row.smtp_mode    = payload.smtp_mode
        row.from_address = payload.from_address
        row.recipients   = payload.recipients
        if payload.smtp_password:
            row.smtp_password_enc = encrypt(payload.smtp_password)
    else:
        row = models.EmailConfig(
            id=1,
            smtp_host=payload.smtp_host,
            smtp_port=payload.smtp_port,
            smtp_user=payload.smtp_user,
            smtp_password_enc=encrypt(payload.smtp_password) if payload.smtp_password else "",
            use_tls=payload.use_tls,
            smtp_mode=payload.smtp_mode,
            from_address=payload.from_address,
            recipients=payload.recipients,
        )
        db.add(row)
    await log_event(db, actor=current_user.username, category="email",
                    action="email.config.save",
                    detail=f"SMTP config saved: {payload.smtp_host}:{payload.smtp_port} ({payload.smtp_mode}), recipients: {payload.recipients}")
    await db.commit()
    await db.refresh(row)
    return _email_row_to_response(row)


@app.post("/api/email/test", tags=["Email Reports"],
          dependencies=[Depends(_auth.require_perm("email_write"))])
async def test_email_config(db: AsyncSession = Depends(get_db),
                             current_user: models.AuthUser = Depends(_auth.get_current_user)):
    """
    Send a lightweight test email to verify SMTP connectivity.
    Uses the saved configuration.
    """
    import asyncio
    from mailer import send_report

    result = await db.execute(
        select(models.EmailConfig).where(models.EmailConfig.id == 1))
    row = result.scalars().first()
    if not row or not row.smtp_host:
        raise HTTPException(400, "Email configuration is not set up yet.")

    recipients = [r.strip() for r in (row.recipients or "").split(",") if r.strip()]
    if not recipients:
        raise HTTPException(400, "No recipients configured.")

    password = decrypt(row.smtp_password_enc) if row.smtp_password_enc else ""

    # Send with dummy single-row server data so the email renders properly
    dummy_server = {
        "server_id": "test", "display_name": "Test Host",
        "ip_address": "0.0.0.0", "hypervisor_type": "—",
        "cpu_usage_pct": 0, "cpu_cores": 0,
        "ram_used_gb": 0, "ram_total_gb": 0, "ram_usage_pct": 0,
        "storage_used_tb": 0, "storage_total_tb": 0, "storage_usage_pct": 0,
        "vm_count": 0, "status": "online",
    }
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None,
            lambda: send_report(
                smtp_host=row.smtp_host,
                smtp_port=row.smtp_port,
                smtp_user=row.smtp_user,
                smtp_password=password,
                use_tls=row.use_tls,
                smtp_mode=row.smtp_mode or "starttls",
                from_address=row.from_address or row.smtp_user,
                recipients=recipients,
                servers=[dummy_server],
                vms=[],
            ),
        )
    except Exception as exc:
        await log_event(db, actor=current_user.username, category="email",
                        action="email.test.fail",
                        detail=f"Test email failed: {exc}", severity="error")
        await db.commit()
        raise HTTPException(500, f"SMTP test failed: {exc}")
    await log_event(db, actor=current_user.username, category="email",
                    action="email.test.ok",
                    detail=f"Test email sent to {', '.join(recipients)}")
    await db.commit()
    return {"status": "ok", "message": f"Test email sent to {recipients}"}


@app.post("/api/email/send-report", tags=["Email Reports"],
          dependencies=[Depends(_auth.require_perm("email_write"))])
async def send_full_report(
    payload: SendReportRequest = SendReportRequest(),
    db: AsyncSession = Depends(get_db),
):
    """
    Fetch live metrics from all (or one specific) enabled hosts, build the
    CSV attachments + HTML summary, and email the report to all recipients.
    """
    import asyncio
    from mailer import send_report

    # ── Load email config ─────────────────────────────────────────────────
    cfg_result = await db.execute(
        select(models.EmailConfig).where(models.EmailConfig.id == 1))
    cfg = cfg_result.scalars().first()
    if not cfg or not cfg.smtp_host:
        raise HTTPException(400, "Email configuration is not set up. Go to Settings → Email.")

    recipients = [r.strip() for r in (cfg.recipients or "").split(",") if r.strip()]
    if not recipients:
        raise HTTPException(400, "No recipients configured in email settings.")

    password = decrypt(cfg.smtp_password_enc) if cfg.smtp_password_enc else ""

    # ── Load server rows ──────────────────────────────────────────────────
    q = select(models.ServerConfig).where(models.ServerConfig.enabled == True)
    if payload.server_id:
        q = q.where(models.ServerConfig.server_id == payload.server_id)
    rows = (await db.execute(q)).scalars().all()
    if not rows:
        raise HTTPException(404, "No enabled servers found.")

    # ── Fetch live metrics concurrently ───────────────────────────────────
    loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(rows)) as pool:
        futures = [loop.run_in_executor(pool, _fetch_live_metrics, r) for r in rows]
        all_metrics = await asyncio.gather(*futures)

    # ── Fetch VM inventory ────────────────────────────────────────────────
    # Re-use get_vms but we already have metrics — extract inline
    meta_result = await db.execute(select(models.VMMetadata))
    static_map = {m.vm_name: m for m in meta_result.scalars().all()}

    servers_data = []
    vms_data     = []
    today        = date.today()
    vm_idx       = 0

    for m in all_metrics:
        # Build server dict for report
        sd = {k: m[k] for k in (
            "server_id", "display_name", "ip_address", "hypervisor_type",
            "cpu_usage_pct", "cpu_cores", "ram_used_gb", "ram_total_gb",
            "ram_usage_pct", "storage_used_tb", "storage_total_tb",
            "storage_usage_pct", "vm_count", "status",
        )}
        servers_data.append(sd)

        for vm in m.get("vms", []):
            meta       = static_map.get(vm["vm_name"])
            ram_total  = float(vm.get("ram_total_gb", 0))
            ram_used   = float(vm.get("ram_used_gb", 0))
            ram_pct    = round(ram_used / max(ram_total, 0.1) * 100, 1)
            cpu_pct    = float(vm.get("cpu_pct", 0))
            power      = vm.get("power_state", "unknown")
            status     = ("stopped" if power == "stopped"
                          else "critical" if cpu_pct >= 90
                          else "warning" if cpu_pct >= 70
                          else "online")
            vms_data.append({
                "vm_name":       vm["vm_name"],
                "ip_address":    vm.get("ip_address", ""),
                "hypervisor_type": m["hypervisor_type"],
                "host_server_id": m["server_id"],
                "power_state":   power,
                "cpu_usage_pct": cpu_pct,
                "cpu_cores":     int(vm.get("cpu_cores", 1)),
                "ram_used_gb":   ram_used,
                "ram_total_gb":  ram_total,
                "ram_usage_pct": ram_pct,
                "owner_name":    meta.owner_name if meta else OWNERS[vm_idx % len(OWNERS)],
                "creation_date": meta.creation_date.isoformat() if meta
                                 else (today - timedelta(days=vm_idx * 30)).isoformat(),
                "purpose":       meta.purpose if meta else PURPOSES[vm_idx % len(PURPOSES)],
                "status":        status,
            })
            vm_idx += 1

    # ── Send ──────────────────────────────────────────────────────────────
    fmt = payload.report_format if payload.report_format in ("html", "csv", "both") else "both"
    try:
        await loop.run_in_executor(
            None,
            lambda: send_report(
                smtp_host=cfg.smtp_host,
                smtp_port=cfg.smtp_port,
                smtp_user=cfg.smtp_user,
                smtp_password=password,
                use_tls=cfg.use_tls,
                smtp_mode=cfg.smtp_mode or "starttls",
                from_address=cfg.from_address or cfg.smtp_user,
                recipients=recipients,
                servers=servers_data,
                vms=vms_data,
                report_format=fmt,
            ),
        )
    except Exception as exc:
        raise HTTPException(500, f"Failed to send report: {exc}")

    return {
        "status":     "ok",
        "recipients": recipients,
        "servers":    len(servers_data),
        "vms":        len(vms_data),
        "format":     fmt,
        "message":    f"Report sent to {len(recipients)} recipient(s) ({fmt} format) with {len(servers_data)} host(s) and {len(vms_data)} VM(s).",
    }


# ──────────────────────────────────────────────────────────────────────────────
# CSV Download endpoints
# ──────────────────────────────────────────────────────────────────────────────

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

def _xlsx_response(content: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        iter([content]),
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/reports/servers.csv", tags=["CSV Downloads"],
         dependencies=[Depends(_auth.require_perm("dashboard_view"))])
async def download_servers_csv(db: AsyncSession = Depends(get_db)):
    """Download infrastructure report as a formatted Excel workbook."""
    from mailer import _build_xlsx
    result = await db.execute(
        select(models.ServerConfig).where(models.ServerConfig.enabled == True))
    rows = result.scalars().all()
    all_metrics = []
    for row in rows:
        cached = metrics_cache.get_cached(row.server_id)
        if cached:
            all_metrics.append(cached)
        else:
            import asyncio, concurrent.futures
            loop = asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                m = await loop.run_in_executor(pool, _fetch_live_metrics, row)
            metrics_cache.set_cached(row.server_id, m)
            all_metrics.append(m)
    # Collect all VMs across all servers for the xlsx builder
    all_vms: list = []
    for m in all_metrics:
        for vm in m.get("vms", []):
            all_vms.append({**vm, "host_server_id": m["server_id"],
                            "hypervisor_type": m["hypervisor_type"]})
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    return _xlsx_response(_build_xlsx(all_metrics, all_vms),
                          f"hypermonitor_report_{ts}.xlsx")


@app.get("/api/reports/vms.csv", tags=["CSV Downloads"],
         dependencies=[Depends(_auth.require_perm("dashboard_view"))])
async def download_vms_csv(db: AsyncSession = Depends(get_db)):
    """Download full VM inventory as a formatted Excel workbook (all hosts)."""
    from mailer import _build_xlsx
    result = await db.execute(
        select(models.ServerConfig).where(models.ServerConfig.enabled == True))
    rows = result.scalars().all()
    meta_result = await db.execute(select(models.VMMetadata))
    static_map = {m.vm_name: m for m in meta_result.scalars().all()}

    servers_data = []
    all_vms      = []
    today        = date.today()
    vm_idx       = 0
    for row in rows:
        cached = metrics_cache.get_cached(row.server_id)
        if not cached:
            import asyncio, concurrent.futures
            loop = asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                cached = await loop.run_in_executor(pool, _fetch_live_metrics, row)
            metrics_cache.set_cached(row.server_id, cached)
        servers_data.append({k: cached[k] for k in (
            "server_id", "display_name", "ip_address", "hypervisor_type",
            "cpu_usage_pct", "cpu_cores", "ram_used_gb", "ram_total_gb",
            "ram_usage_pct", "storage_used_tb", "storage_total_tb",
            "storage_usage_pct", "vm_count", "status",
        )})
        for vm in cached.get("vms", []):
            meta = static_map.get(vm["vm_name"])
            ram_total = float(vm.get("ram_total_gb", 0))
            ram_used  = float(vm.get("ram_used_gb",  0))
            all_vms.append({
                "vm_name":        vm["vm_name"],
                "ip_address":     vm.get("ip_address", ""),
                "hypervisor_type": cached["hypervisor_type"],
                "host_server_id": cached["server_id"],
                "power_state":    vm.get("power_state", ""),
                "cpu_usage_pct":  float(vm.get("cpu_pct", 0)),
                "cpu_cores":      int(vm.get("cpu_cores", 1)),
                "ram_used_gb":    ram_used,
                "ram_total_gb":   ram_total,
                "ram_usage_pct":  round(ram_used / max(ram_total, 0.1) * 100, 1),
                "owner_name":     meta.owner_name if meta else OWNERS[vm_idx % len(OWNERS)],
                "creation_date":  meta.creation_date.isoformat() if meta
                                  else (today - timedelta(days=vm_idx * 30)).isoformat(),
                "purpose":        meta.purpose if meta else PURPOSES[vm_idx % len(PURPOSES)],
                "status":         "stopped" if vm.get("power_state") == "stopped" else "online",
            })
            vm_idx += 1

    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    return _xlsx_response(_build_xlsx(servers_data, all_vms),
                          f"hypermonitor_report_{ts}.xlsx")


@app.get("/api/reports/vms/{server_id}.csv", tags=["CSV Downloads"],
         dependencies=[Depends(_auth.require_perm("dashboard_view"))])
async def download_server_vms_csv(server_id: str,
                                   db: AsyncSession = Depends(get_db)):
    """Download VM inventory for a single host as a formatted Excel workbook."""
    from mailer import _build_xlsx
    result = await db.execute(
        select(models.ServerConfig).where(models.ServerConfig.server_id == server_id))
    row = result.scalars().first()
    if not row:
        raise HTTPException(404, f"Server '{server_id}' not found")

    cached = metrics_cache.get_cached(server_id)
    if not cached:
        import asyncio, concurrent.futures
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            cached = await loop.run_in_executor(pool, _fetch_live_metrics, row)
        metrics_cache.set_cached(server_id, cached)

    meta_result = await db.execute(select(models.VMMetadata))
    static_map = {m.vm_name: m for m in meta_result.scalars().all()}

    server_data = [{k: cached[k] for k in (
        "server_id", "display_name", "ip_address", "hypervisor_type",
        "cpu_usage_pct", "cpu_cores", "ram_used_gb", "ram_total_gb",
        "ram_usage_pct", "storage_used_tb", "storage_total_tb",
        "storage_usage_pct", "vm_count", "status",
    )}]
    vms_data = []
    today    = date.today()
    for i, vm in enumerate(cached.get("vms", [])):
        meta = static_map.get(vm["vm_name"])
        ram_total = float(vm.get("ram_total_gb", 0))
        ram_used  = float(vm.get("ram_used_gb",  0))
        vms_data.append({
            "vm_name":        vm["vm_name"],
            "ip_address":     vm.get("ip_address", ""),
            "hypervisor_type": cached["hypervisor_type"],
            "host_server_id": server_id,
            "power_state":    vm.get("power_state", ""),
            "cpu_usage_pct":  float(vm.get("cpu_pct", 0)),
            "cpu_cores":      int(vm.get("cpu_cores", 1)),
            "ram_used_gb":    ram_used,
            "ram_total_gb":   ram_total,
            "ram_usage_pct":  round(ram_used / max(ram_total, 0.1) * 100, 1),
            "owner_name":     meta.owner_name if meta else "",
            "creation_date":  meta.creation_date.isoformat() if meta
                              else (today - timedelta(days=i * 30)).isoformat(),
            "purpose":        meta.purpose if meta else "",
            "status":         "stopped" if vm.get("power_state") == "stopped" else "online",
        })

    sname = re.sub(r"[^a-z0-9]+", "_", row.display_name.lower()).strip("_")[:30]
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    return _xlsx_response(_build_xlsx(server_data, vms_data),
                          f"hypermonitor_{sname}_{ts}.xlsx")


@app.get("/api/reports/report.html", tags=["CSV Downloads"],
         dependencies=[Depends(_auth.require_perm("dashboard_view"))])
async def download_html_report(db: AsyncSession = Depends(get_db)):
    """
    Download the full HTML report as a standalone file.
    Uses the same data pipeline as /api/email/send-report.
    """
    from mailer import build_html_report
    import asyncio, concurrent.futures

    q = select(models.ServerConfig).where(models.ServerConfig.enabled == True)
    rows = (await db.execute(q)).scalars().all()
    if not rows:
        raise HTTPException(404, "No enabled servers found.")

    loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(len(rows), 1)) as pool:
        futures = [loop.run_in_executor(pool, _fetch_live_metrics, r) for r in rows]
        all_metrics = await asyncio.gather(*futures)

    meta_result = await db.execute(select(models.VMMetadata))
    static_map  = {m.vm_name: m for m in meta_result.scalars().all()}

    servers_data, vms_data = [], []
    today  = date.today()
    vm_idx = 0
    for m in all_metrics:
        servers_data.append({k: m[k] for k in (
            "server_id", "display_name", "ip_address", "hypervisor_type",
            "cpu_usage_pct", "cpu_cores", "ram_used_gb", "ram_total_gb",
            "ram_usage_pct", "storage_used_tb", "storage_total_tb",
            "storage_usage_pct", "vm_count", "status",
        )})
        for vm in m.get("vms", []):
            meta      = static_map.get(vm["vm_name"])
            ram_total = float(vm.get("ram_total_gb", 0))
            ram_used  = float(vm.get("ram_used_gb",  0))
            vms_data.append({
                "vm_name":        vm["vm_name"],
                "ip_address":     vm.get("ip_address", ""),
                "hypervisor_type": m["hypervisor_type"],
                "host_server_id": m["server_id"],
                "power_state":    vm.get("power_state", ""),
                "cpu_usage_pct":  float(vm.get("cpu_pct", 0)),
                "cpu_cores":      int(vm.get("cpu_cores", 1)),
                "ram_used_gb":    ram_used,
                "ram_total_gb":   ram_total,
                "ram_usage_pct":  round(ram_used / max(ram_total, 0.1) * 100, 1),
                "owner_name":     meta.owner_name if meta else OWNERS[vm_idx % len(OWNERS)],
                "creation_date":  meta.creation_date.isoformat() if meta
                                  else (today - timedelta(days=vm_idx * 30)).isoformat(),
                "purpose":        meta.purpose if meta else PURPOSES[vm_idx % len(PURPOSES)],
                "status":         "stopped" if vm.get("power_state") == "stopped" else "online",
            })
            vm_idx += 1

    html_bytes = build_html_report(servers_data, vms_data)
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    return StreamingResponse(
        iter([html_bytes]),
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="hypermonitor_report_{ts}.html"'},
    )


# ──────────────────────────────────────────────────────────────────────────────
# Email Schedule CRUD + helper
# ──────────────────────────────────────────────────────────────────────────────

class EmailSchedulePayload(BaseModel):
    schedule_type: str  = "disabled"  # "daily" | "weekly" | "disabled"
    hour:          int  = 8
    minute:        int  = 0
    day_of_week:   int  = 0           # 0=Mon … 6=Sun
    enabled:       bool = True


class EmailScheduleResponse(BaseModel):
    schedule_type: str
    hour:          int
    minute:        int
    day_of_week:   int
    enabled:       bool
    next_run:      Optional[str]
    last_sent_at:  Optional[str]


def _register_schedule(row: models.EmailSchedule) -> None:
    """Register the APScheduler job from a DB row."""
    if not row.enabled or row.schedule_type == "disabled":
        report_scheduler.remove_job()
        return

    async def _job():
        """Async job: build + send the full report."""
        from mailer import send_report
        async with SessionLocal() as db:
            cfg_r = await db.execute(
                select(models.EmailConfig).where(models.EmailConfig.id == 1))
            cfg = cfg_r.scalars().first()
            if not cfg or not cfg.smtp_host:
                return
            recipients = [r.strip() for r in (cfg.recipients or "").split(",") if r.strip()]
            if not recipients:
                return
            password = decrypt(cfg.smtp_password_enc) if cfg.smtp_password_enc else ""

            server_rows_r = await db.execute(
                select(models.ServerConfig).where(models.ServerConfig.enabled == True))
            server_rows = server_rows_r.scalars().all()

            meta_r = await db.execute(select(models.VMMetadata))
            static_map = {m.vm_name: m for m in meta_r.scalars().all()}

        import asyncio, concurrent.futures
        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(len(server_rows), 8)
        ) as pool:
            futures = [loop.run_in_executor(pool, _fetch_live_metrics, r)
                       for r in server_rows]
            all_metrics = await asyncio.gather(*futures)

        servers_data, vms_data = [], []
        today = date.today()
        vm_idx = 0
        for m in all_metrics:
            servers_data.append({k: m[k] for k in (
                "server_id", "display_name", "ip_address", "hypervisor_type",
                "cpu_usage_pct", "cpu_cores", "ram_used_gb", "ram_total_gb",
                "ram_usage_pct", "storage_used_tb", "storage_total_tb",
                "storage_usage_pct", "vm_count", "status",
            )})
            for vm in m.get("vms", []):
                meta = static_map.get(vm["vm_name"])
                ram_total = float(vm.get("ram_total_gb", 0))
                ram_used  = float(vm.get("ram_used_gb",  0))
                vms_data.append({
                    "vm_name": vm["vm_name"],
                    "ip_address": vm.get("ip_address", ""),
                    "hypervisor_type": m["hypervisor_type"],
                    "host_server_id": m["server_id"],
                    "power_state": vm.get("power_state", ""),
                    "cpu_usage_pct": float(vm.get("cpu_pct", 0)),
                    "cpu_cores": int(vm.get("cpu_cores", 1)),
                    "ram_used_gb": ram_used, "ram_total_gb": ram_total,
                    "ram_usage_pct": round(ram_used / max(ram_total, 0.1) * 100, 1),
                    "owner_name": meta.owner_name if meta else OWNERS[vm_idx % len(OWNERS)],
                    "creation_date": meta.creation_date.isoformat() if meta
                                     else (today - timedelta(days=vm_idx * 30)).isoformat(),
                    "purpose": meta.purpose if meta else PURPOSES[vm_idx % len(PURPOSES)],
                    "status": "stopped" if vm.get("power_state") == "stopped" else "online",
                })
                vm_idx += 1

        import asyncio as _asyncio
        loop2 = _asyncio.get_running_loop()
        await loop2.run_in_executor(
            None,
            lambda: send_report(
                smtp_host=cfg.smtp_host, smtp_port=cfg.smtp_port,
                smtp_user=cfg.smtp_user, smtp_password=password,
                use_tls=cfg.use_tls,
                smtp_mode=cfg.smtp_mode or "starttls",
                from_address=cfg.from_address or cfg.smtp_user,
                recipients=recipients, servers=servers_data, vms=vms_data,
            ),
        )
        # Update last_sent_at
        from datetime import datetime, timezone as tz
        async with SessionLocal() as db:
            sched_r = await db.execute(
                select(models.EmailSchedule).where(models.EmailSchedule.id == 1))
            sched = sched_r.scalars().first()
            if sched:
                sched.last_sent_at = datetime.now(tz.utc).replace(tzinfo=None)
                await db.commit()

    if row.schedule_type == "daily":
        report_scheduler.schedule_daily(row.hour, row.minute, _job)
    elif row.schedule_type == "weekly":
        report_scheduler.schedule_weekly(row.day_of_week, row.hour, row.minute, _job)


@app.get("/api/email/schedule", response_model=EmailScheduleResponse,
         tags=["Email Reports"],
         dependencies=[Depends(_auth.require_perm("email_view"))])
async def get_schedule(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(models.EmailSchedule).where(models.EmailSchedule.id == 1))
    row = result.scalars().first()
    if not row:
        return EmailScheduleResponse(
            schedule_type="disabled", hour=8, minute=0,
            day_of_week=0, enabled=False, next_run=None, last_sent_at=None,
        )
    return EmailScheduleResponse(
        schedule_type=row.schedule_type,
        hour=row.hour, minute=row.minute, day_of_week=row.day_of_week,
        enabled=row.enabled,
        next_run=report_scheduler.get_next_run(),
        last_sent_at=row.last_sent_at.isoformat() if row.last_sent_at else None,
    )


@app.put("/api/email/schedule", response_model=EmailScheduleResponse,
         tags=["Email Reports"],
         dependencies=[Depends(_auth.require_perm("email_write"))])
async def save_schedule(payload: EmailSchedulePayload,
                        db: AsyncSession = Depends(get_db)):
    """Create or update the report schedule and (re)register the APScheduler job."""
    result = await db.execute(
        select(models.EmailSchedule).where(models.EmailSchedule.id == 1))
    row = result.scalars().first()
    if row:
        row.schedule_type = payload.schedule_type
        row.hour          = payload.hour
        row.minute        = payload.minute
        row.day_of_week   = payload.day_of_week
        row.enabled       = payload.enabled
    else:
        row = models.EmailSchedule(
            id=1,
            schedule_type=payload.schedule_type,
            hour=payload.hour, minute=payload.minute,
            day_of_week=payload.day_of_week, enabled=payload.enabled,
        )
        db.add(row)
    await db.commit()
    await db.refresh(row)
    _register_schedule(row)
    return EmailScheduleResponse(
        schedule_type=row.schedule_type,
        hour=row.hour, minute=row.minute, day_of_week=row.day_of_week,
        enabled=row.enabled,
        next_run=report_scheduler.get_next_run(),
        last_sent_at=row.last_sent_at.isoformat() if row.last_sent_at else None,
    )


@app.delete("/api/email/schedule", status_code=204, tags=["Email Reports"],
            dependencies=[Depends(_auth.require_perm("email_write"))])
async def delete_schedule(db: AsyncSession = Depends(get_db)):
    """Disable the schedule and remove the APScheduler job."""
    result = await db.execute(
        select(models.EmailSchedule).where(models.EmailSchedule.id == 1))
    row = result.scalars().first()
    if row:
        row.enabled = False
        row.schedule_type = "disabled"
        await db.commit()
    report_scheduler.remove_job()


# ──────────────────────────────────────────────────────────────────────────────
# Req 3 — Snapshot routes
# ──────────────────────────────────────────────────────────────────────────────

def _snap_to_response(s: models.VMSnapshot) -> SnapshotResponse:
    extra: Dict[str, Any] = {}
    try:
        extra = json.loads(s.extra or "{}")
    except (ValueError, TypeError):
        pass
    return SnapshotResponse(
        id=s.id,
        server_id=s.server_id,
        vm_id=s.vm_id,
        vm_name=s.vm_name,
        snap_name=s.snap_name,
        created_at=s.created_at.isoformat() if s.created_at else None,
        size_bytes=s.size_bytes or 0,
        hypervisor_type=s.hypervisor_type,
        extra=extra,
        fetched_at=s.fetched_at.isoformat() if s.fetched_at else "",
    )


@app.get("/api/snapshots", response_model=List[SnapshotResponse],
         tags=["Snapshots"],
         dependencies=[Depends(_auth.require_perm("dashboard_view"))])
async def list_snapshots(
    db:              AsyncSession = Depends(get_db),
    server_id:       Optional[str] = Query(None, description="Filter by server"),
    vm_id:           Optional[str] = Query(None, description="Filter by VM id"),
    vm_name:         Optional[str] = Query(None, description="Filter by VM name (substring)"),
    hypervisor_type: Optional[str] = Query(None, description="Filter by hypervisor type"),
):
    """
    Return persisted snapshot records.  All filters are optional and additive.
    Call POST /api/snapshots/ingest to populate this table from a live host.
    """
    q = select(models.VMSnapshot)
    if server_id:       q = q.where(models.VMSnapshot.server_id == server_id)
    if vm_id:           q = q.where(models.VMSnapshot.vm_id == vm_id)
    if vm_name:         q = q.where(models.VMSnapshot.vm_name.ilike(f"%{vm_name}%"))
    if hypervisor_type: q = q.where(models.VMSnapshot.hypervisor_type == hypervisor_type)
    q = q.order_by(models.VMSnapshot.created_at.desc().nulls_last())
    result = await db.execute(q)
    return [_snap_to_response(s) for s in result.scalars().all()]


@app.post("/api/snapshots/ingest", response_model=List[SnapshotResponse],
          tags=["Snapshots"], status_code=201,
          dependencies=[Depends(_auth.require_perm("servers_view"))])
async def ingest_snapshots(
    payload: IngestSnapshotsRequest,
    db:      AsyncSession = Depends(get_db),
):
    """
    Connect to the hypervisor, fetch all snapshots for a named VM, and
    upsert them into the vm_snapshot table.

    The correct adapter is selected from REGISTRY — no if/else branching.
    Existing records for the same (server_id, vm_name) are replaced on each
    ingest so the table always reflects current hypervisor state.
    """
    import asyncio as _asyncio

    srv_result = await db.execute(
        select(models.ServerConfig).where(
            models.ServerConfig.server_id == payload.server_id
        )
    )
    srv = srv_result.scalars().first()
    if not srv:
        raise HTTPException(404, f"Server '{payload.server_id}' not found.")

    username = decrypt(srv.username_enc)
    password = decrypt(srv.password_enc)
    if not username or not password:
        raise HTTPException(400, "Server has no credentials stored.")

    # Run the blocking adapter call in a thread
    loop = _asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        adapter  = get_adapter(srv, username, password)
        raw_snaps: List[Dict] = await loop.run_in_executor(
            pool, adapter.get_vm_snapshots, payload.vm_name
        )

    # Normalize and derive vm_id
    short    = srv.hypervisor_type.replace(" ", "").replace("-", "")[:4].lower()
    safe_name = re.sub(r"[^a-z0-9]", "-", payload.vm_name.lower())[:20]
    vm_id    = f"{short}-{safe_name}"

    # Delete stale snapshots for this VM before re-inserting
    await db.execute(
        models.VMSnapshot.__table__.delete().where(
            (models.VMSnapshot.server_id == payload.server_id) &
            (models.VMSnapshot.vm_name   == payload.vm_name)
        )
    )

    from datetime import datetime as _dt
    saved: List[models.VMSnapshot] = []
    for snap in raw_snaps:
        created: Optional[_dt] = None
        raw_ts = snap.get("created_at", "")
        if raw_ts:
            try:
                created = _dt.fromisoformat(raw_ts.replace("Z", "+00:00"))
                created = created.replace(tzinfo=None)   # store naive UTC
            except ValueError:
                created = None

        row = models.VMSnapshot(
            server_id=payload.server_id,
            vm_id=vm_id,
            vm_name=payload.vm_name,
            snap_name=snap.get("snap_name", ""),
            created_at=created,
            size_bytes=int(snap.get("size_bytes", 0)),
            hypervisor_type=srv.hypervisor_type,
            extra=json.dumps(snap.get("extra", {})),
        )
        db.add(row)
        saved.append(row)

    await db.commit()
    for row in saved:
        await db.refresh(row)
    return [_snap_to_response(s) for s in saved]


# ──────────────────────────────────────────────────────────────────────────────
# Req 3 — VM Event routes
# ──────────────────────────────────────────────────────────────────────────────

def _event_to_response(e: models.VMEvent) -> VMEventResponse:
    meta: Dict[str, Any] = {}
    try:
        meta = json.loads(e.event_metadata or "{}")
    except (ValueError, TypeError):
        pass
    return VMEventResponse(
        id=e.id,
        server_id=e.server_id,
        vm_id=e.vm_id or "",
        vm_name=e.vm_name or "",
        event_type=e.event_type or "",
        severity=e.severity or "info",
        message=e.message or "",
        occurred_at=e.occurred_at.isoformat() if e.occurred_at else None,
        hypervisor_type=e.hypervisor_type or "",
        metadata=meta,
        ingested_at=e.ingested_at.isoformat() if e.ingested_at else "",
    )


@app.get("/api/vm-events", response_model=List[VMEventResponse],
         tags=["VM Events"],
         dependencies=[Depends(_auth.require_perm("events_view"))])
async def list_vm_events(
    db:              AsyncSession = Depends(get_db),
    server_id:       Optional[str] = Query(None),
    vm_id:           Optional[str] = Query(None),
    vm_name:         Optional[str] = Query(None),
    event_type:      Optional[str] = Query(None),
    severity:        Optional[str] = Query(None),
    hypervisor_type: Optional[str] = Query(None),
    limit:           int           = Query(200, ge=1, le=2000),
):
    """
    List VM events.  All filters are optional and additive.
    Results ordered by occurred_at DESC (newest first).
    """
    q = select(models.VMEvent)
    if server_id:       q = q.where(models.VMEvent.server_id == server_id)
    if vm_id:           q = q.where(models.VMEvent.vm_id == vm_id)
    if vm_name:         q = q.where(models.VMEvent.vm_name.ilike(f"%{vm_name}%"))
    if event_type:      q = q.where(models.VMEvent.event_type == event_type)
    if severity:        q = q.where(models.VMEvent.severity == severity)
    if hypervisor_type: q = q.where(models.VMEvent.hypervisor_type == hypervisor_type)
    q = q.order_by(models.VMEvent.occurred_at.desc().nulls_last()).limit(limit)
    result = await db.execute(q)
    return [_event_to_response(e) for e in result.scalars().all()]


@app.post("/api/vm-events", response_model=VMEventResponse,
          tags=["VM Events"], status_code=201,
          dependencies=[Depends(_auth.require_perm("events_write"))])
async def ingest_event(
    payload: IngestEventRequest,
    db:      AsyncSession = Depends(get_db),
):
    """
    Ingest a single normalized VM / hypervisor event.

    The metadata dict is stored as JSON in the 'metadata' column (JSONB on
    Postgres).  Any number of arbitrary key-value pairs are accepted so new
    hypervisors never require a schema migration.

    Example body:
    {
      "server_id": "prod-esxi-abc123",
      "vm_name": "web-01",
      "event_type": "snapshot.create",
      "severity": "info",
      "message": "Snapshot 'pre-patch' created by admin",
      "occurred_at": "2024-06-01T14:22:00Z",
      "hypervisor_type": "VMware ESXi",
      "metadata": {
        "task_id": "task-8812",
        "user": "admin@vsphere.local",
        "quiesced": true
      }
    }
    """
    from datetime import datetime as _dt
    occurred: Optional[_dt] = None
    if payload.occurred_at:
        try:
            occurred = _dt.fromisoformat(
                payload.occurred_at.replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except ValueError:
            occurred = None

    row = models.VMEvent(
        server_id=payload.server_id,
        vm_id=payload.vm_id or "",
        vm_name=payload.vm_name or "",
        event_type=payload.event_type,
        severity=payload.severity,
        message=payload.message,
        occurred_at=occurred,
        hypervisor_type=payload.hypervisor_type,
        event_metadata=json.dumps(payload.metadata),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _event_to_response(row)


# ──────────────────────────────────────────────────────────────────────────────
# Health
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    # NOTE: workers=1 is intentional. The in-memory metrics cache is process-local;
    # multiple workers would each maintain a separate cache, causing redundant
    # hypervisor polls and inconsistent responses between requests.
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True, workers=1)
