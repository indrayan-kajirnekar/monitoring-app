"""
hypervisors/base.py — Abstract Strategy Interface for all hypervisor adapters.

Every concrete adapter (ESXi, KVM, Hyper-V, Proxmox, …) inherits this class
and implements its three abstract methods.  The rest of the application only
ever speaks to HypervisorInterface — it has zero knowledge of what protocol
or SDK is underneath.

Unified return schemas
──────────────────────
All methods return plain dicts whose keys are documented here so that the
dispatcher in main.py / cache layer can handle them uniformly.

ServerStatusDict
    server_id          str
    hostname           str
    display_name       str
    hypervisor_type    str
    ip_address         str
    cpu_usage_pct      float          (0–100)
    cpu_cores          int
    ram_used_gb        float
    ram_total_gb       float
    ram_usage_pct      float          (0–100)
    storage_used_tb    float
    storage_total_tb   float
    storage_usage_pct  float          (0–100)
    drives             list[DriveDict]
    vm_count           int
    vms                list[VMDict]   (embedded, same as get_all_vms())
    status             str            "online" | "warning" | "critical"
    error              str | None

VMDict
    vm_name      str
    ip_address   str
    power_state  str      "running" | "stopped" | "paused" | "unknown"
    cpu_cores    int
    cpu_pct      float
    ram_used_gb  float
    ram_total_gb float

SnapshotDict
    vm_name      str
    snap_name    str
    created_at   str      ISO-8601 UTC timestamp
    size_bytes   int      normalized to bytes; 0 when unknown

DriveDict
    name         str
    total_gb     float
    used_gb      float
    free_gb      float
    usage_pct    float
"""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import models


class HypervisorInterface(abc.ABC):
    """
    Abstract base class that every hypervisor adapter must implement.

    Parameters (stored as instance attributes for use by all methods)
    ──────────────────────────────────────────────────────────────────
    row       – models.ServerConfig ORM row (ip_address, display_name, …)
    username  – plaintext credential (already decrypted by the caller)
    password  – plaintext credential (already decrypted by the caller)
    """

    def __init__(self,
                 row: "models.ServerConfig",
                 username: str,
                 password: str) -> None:
        self.row      = row
        self.username = username
        self.password = password
        self.ip       = row.ip_address

    # ── Required implementations ──────────────────────────────────────────────

    @abc.abstractmethod
    def get_server_status(self) -> dict[str, Any]:
        """
        Return a ServerStatusDict with current host-level metrics AND an
        embedded 'vms' list (list of VMDict).  This is the main polling
        call used by the background cache poller.
        """

    @abc.abstractmethod
    def get_all_vms(self) -> list[dict[str, Any]]:
        """
        Return a list of VMDict for every VM known to this host.
        May be derived from the cached result of get_server_status() or
        fetched independently — the choice is left to the adapter.
        """

    @abc.abstractmethod
    def get_vm_snapshots(self, vm_name: str) -> list[dict[str, Any]]:
        """
        Return a list of SnapshotDict for the named VM.
        Returns an empty list if snapshots are not supported or the VM
        does not exist.  Must never raise — return [] on any error.
        """

    # ── Helpers available to all adapters ────────────────────────────────────

    @staticmethod
    def status_from_cpu(cpu_pct: float) -> str:
        """Derive status label from CPU utilisation percentage."""
        if cpu_pct >= 90:
            return "critical"
        if cpu_pct >= 70:
            return "warning"
        return "online"

    def _fallback(self, error: str = "") -> dict[str, Any]:
        """
        Return a safe all-zeros ServerStatusDict when the connection fails.
        Callers should set error= to the exception message.
        """
        row = self.row
        return {
            "server_id":         row.server_id,
            "hostname":          row.hostname or self.ip,
            "display_name":      row.display_name,
            "hypervisor_type":   row.hypervisor_type,
            "ip_address":        self.ip,
            "cpu_usage_pct":     0.0,
            "cpu_cores":         int(row.cpu_cores or 1),
            "ram_used_gb":       0.0,
            "ram_total_gb":      float(row.ram_total_gb  or 256),
            "ram_usage_pct":     0.0,
            "storage_used_tb":   0.0,
            "storage_total_tb":  float(row.storage_total_tb or 10),
            "storage_usage_pct": 0.0,
            "drives":            [],
            "vm_count":          0,
            "vms":               [],
            "status":            "critical",
            "error":             error,
        }
