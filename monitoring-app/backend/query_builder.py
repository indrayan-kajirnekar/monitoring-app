"""
query_builder.py — Extensible Builder Pattern for the VM inventory search API.

Usage
─────
    from query_builder import VMQueryBuilder

    results = (
        VMQueryBuilder(all_vms)
        .filter_hypervisor("VMware ESXi")
        .filter_server("prod-esxi-abc123")
        .search("192.168.1")          # auto-detects: IP substring match
        .filter_power_state("running")
        .build()
    )

Design
──────
• Each .filter_*() / .search() call pushes a predicate (Callable[[dict], bool])
  onto an internal list.
• .build() applies all predicates with a logical AND across all VMs.
• search() auto-classifies the query string:
    - Looks like an IP (digits + dots / colons)  → match ip_address substring
    - Looks like a server_id slug (hyphenated)   → match host_server_id exactly
    - Otherwise                                  → OR across vm_name + owner_name
                                                   + purpose (case-insensitive)
• Merging filters is additive: every chain call narrows the result set.
  This eliminates every nested if/else statement in route handlers.

Extending
─────────
Add a new dimension by adding a method:

    def filter_status(self, status: str) -> "VMQueryBuilder":
        if status:
            self._predicates.append(lambda vm: vm.get("status") == status)
        return self
"""

from __future__ import annotations

import re
from typing import Any, Callable


# Regex that recognises IPv4, IPv4 prefix, or IPv6 fragments
_IP_PATTERN = re.compile(
    r"^(\d{1,3}\.){1,3}\d{0,3}$"    # IPv4 / IPv4 prefix  e.g. "192.168.1" or "10.0.0.1"
    r"|^[\da-fA-F:]{2,39}$"          # IPv6 or partial IPv6
)


class VMQueryBuilder:
    """
    Composable, predicate-driven filter chain for VM records.

    Works on plain dicts (as returned by /api/vms) — no ORM dependency.
    """

    def __init__(self, vms: list[dict[str, Any]]) -> None:
        self._vms: list[dict[str, Any]] = vms
        self._predicates: list[Callable[[dict[str, Any]], bool]] = []

    # ── Dimension filters (each returns self for chaining) ────────────────────

    def filter_hypervisor(self, hypervisor_type: str | None) -> "VMQueryBuilder":
        """Narrow to a specific hypervisor type; no-op when None or empty."""
        if hypervisor_type:
            self._predicates.append(
                lambda vm, ht=hypervisor_type: vm.get("hypervisor_type") == ht
            )
        return self

    def filter_server(self, server_id: str | None) -> "VMQueryBuilder":
        """Narrow to VMs belonging to a specific host server_id."""
        if server_id:
            self._predicates.append(
                lambda vm, sid=server_id: vm.get("host_server_id") == sid
            )
        return self

    def filter_power_state(self, power_state: str | None) -> "VMQueryBuilder":
        """Narrow to a specific power state: running | stopped | paused."""
        if power_state and power_state != "all":
            self._predicates.append(
                lambda vm, ps=power_state: vm.get("power_state") == ps
            )
        return self

    def filter_status(self, status: str | None) -> "VMQueryBuilder":
        """Narrow to a specific status: online | warning | critical | stopped."""
        if status and status != "all":
            self._predicates.append(
                lambda vm, st=status: vm.get("status") == st
            )
        return self

    # ── Smart global search ───────────────────────────────────────────────────

    def search(self, query: str | None) -> "VMQueryBuilder":
        """
        Auto-classify the query string and apply an OR match:

        IP-like string  → substring match on vm.ip_address
        Hyphenated slug → exact match on vm.host_server_id
        Anything else   → OR across vm_name, owner_name, purpose (case-insensitive)

        A blank or None query is a no-op (returns self unchanged).
        """
        if not query:
            return self

        q = query.strip()
        if not q:
            return self

        if _IP_PATTERN.match(q):
            # IP-prefix or full IP — substring search on ip_address
            self._predicates.append(
                lambda vm, _q=q: _q in (vm.get("ip_address") or "")
            )
        elif re.match(r"^[a-z0-9]+-[a-z0-9-]+$", q, re.IGNORECASE):
            # Slug pattern (e.g. "prod-kvm-7f2a1b") — exact server_id match
            self._predicates.append(
                lambda vm, _q=q: (
                    vm.get("host_server_id", "").lower() == _q.lower()
                    or vm.get("vm_id", "").lower() == _q.lower()
                )
            )
        else:
            # Free-text: OR across name / owner / purpose
            ql = q.lower()
            self._predicates.append(
                lambda vm, _ql=ql: (
                    _ql in (vm.get("vm_name")    or "").lower()
                    or _ql in (vm.get("owner_name") or "").lower()
                    or _ql in (vm.get("purpose")    or "").lower()
                    or _ql in (vm.get("ip_address") or "").lower()
                )
            )
        return self

    # ── Terminal operation ────────────────────────────────────────────────────

    def build(self) -> list[dict[str, Any]]:
        """
        Apply all accumulated predicates (logical AND) and return the
        filtered list.  Does not mutate the original list.
        """
        if not self._predicates:
            return list(self._vms)

        return [
            vm for vm in self._vms
            if all(pred(vm) for pred in self._predicates)
        ]

    def __len__(self) -> int:
        return len(self.build())
