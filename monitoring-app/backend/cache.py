"""
cache.py — In-memory metrics cache for HyperMonitor.

Purpose
───────
Hypervisor queries (SSH/WinRM/pyVmomi) take 2-10 seconds each.
Without a cache, every browser tab polling /api/servers or /api/vms
triggers a full round-trip to every hypervisor. With 3 servers and 3
browser tabs open, that is 9 simultaneous hypervisor connections every
10 seconds — completely unnecessary.

Solution: a simple TTL cache keyed by (endpoint, server_id).
  • GET /api/servers  — cached 30 s
  • GET /api/vms      — cached 30 s  (same underlying data)
  • GET /api/hypervisors — derived from servers cache instantly

The cache is filled by background refresh tasks started at startup
(one asyncio task per enabled server) and also refreshed on-demand
the first time data is requested for a newly added server.

Thread-safety: asyncio tasks write to the cache; FastAPI async handlers
read from it. All access is from the same event-loop thread, so no lock
is needed for dict reads/writes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Cache store
# ──────────────────────────────────────────────────────────────────────────────

# { server_id: {"data": dict, "ts": float} }
_metrics_cache: Dict[str, Dict[str, Any]] = {}

# How long cached data is considered fresh (seconds).
# Hypervisor queries take 2-10 s each — 60 s TTL means at most 1 full poll per
# minute per server, keeping the dashboard responsive without hammering hosts.
CACHE_TTL = 60


def get_cached(server_id: str) -> Optional[Dict]:
    """Return fresh cached metrics dict if within TTL, else None."""
    entry = _metrics_cache.get(server_id)
    if entry and (time.monotonic() - entry["ts"]) < CACHE_TTL:
        return entry["data"]
    return None


def get_cached_stale(server_id: str) -> Optional[Dict]:
    """
    Return cached metrics regardless of age (stale-while-revalidate).
    Used by routes to immediately return last-known data while a background
    re-poll is in flight, eliminating the blank-screen on page reload.
    """
    entry = _metrics_cache.get(server_id)
    return entry["data"] if entry else None


def set_cached(server_id: str, data: Dict) -> None:
    """Store metrics dict with current timestamp."""
    _metrics_cache[server_id] = {"data": data, "ts": time.monotonic()}


def get_all_cached() -> List[Dict]:
    """Return all cached metric dicts regardless of freshness (show last known)."""
    return [entry["data"] for entry in _metrics_cache.values()]


def evict(server_id: str) -> None:
    """Remove a server from the cache (called on delete / disable)."""
    _metrics_cache.pop(server_id, None)


def cache_age(server_id: str) -> Optional[float]:
    """Return seconds since last successful poll for a server, or None."""
    entry = _metrics_cache.get(server_id)
    return (time.monotonic() - entry["ts"]) if entry else None


# ──────────────────────────────────────────────────────────────────────────────
# Background poller
# ──────────────────────────────────────────────────────────────────────────────

# Holds the single background-refresh task reference
_refresh_task: Optional[asyncio.Task] = None

# How often the background loop re-polls ALL enabled servers (seconds).
# Matches CACHE_TTL so data never goes stale between cycles.
POLL_INTERVAL = 60


async def _poll_loop(fetch_fn, get_rows_fn) -> None:
    """
    Continuously poll all enabled servers and update the cache.

    Parameters
    ──────────
    fetch_fn    – callable(row) → dict  (the synchronous _fetch_live_metrics)
    get_rows_fn – async callable() → list[ServerConfig rows]
    """
    import concurrent.futures

    loop = asyncio.get_event_loop()
    log.info("Background metrics poller started (interval=%ds)", POLL_INTERVAL)

    while True:
        try:
            rows = await get_rows_fn()
            if rows:
                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(len(rows), 8)
                ) as pool:
                    futures = {
                        loop.run_in_executor(pool, fetch_fn, r): r.server_id
                        for r in rows
                    }
                    for fut, sid in futures.items():
                        try:
                            result = await fut
                            set_cached(sid, result)
                        except Exception as exc:
                            log.warning("Poll failed for %s: %s", sid, exc)
        except Exception as exc:
            log.error("Background poll loop error: %s", exc)

        await asyncio.sleep(POLL_INTERVAL)


def start_background_poller(fetch_fn, get_rows_fn) -> None:
    """
    Start the background poll loop as a daemon asyncio task.
    Safe to call from FastAPI startup handler.
    """
    global _refresh_task
    _refresh_task = asyncio.create_task(_poll_loop(fetch_fn, get_rows_fn))
    log.info("Background poller task created.")


def stop_background_poller() -> None:
    """Cancel the background task (called on app shutdown)."""
    global _refresh_task
    if _refresh_task and not _refresh_task.done():
        _refresh_task.cancel()
        _refresh_task = None
