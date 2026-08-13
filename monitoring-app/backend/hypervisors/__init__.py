"""
hypervisors/ — Plug-and-play hypervisor adapter package.

Usage
─────
    from hypervisors import get_adapter

    adapter = get_adapter(row)           # picks the right class from the registry
    metrics = adapter.get_server_status()  # returns a unified dict

Adding Proxmox VE (or any future hypervisor)
─────────────────────────────────────────────
1.  hypervisors/proxmox.py already exists — uncomment the two Proxmox lines below.
2.  Install the extra dependency:  pip install proxmoxer requests
    (or add it to requirements.txt)
3.  That's it — zero changes to main.py, cache.py, routes, or the UI.
    The "Proxmox VE" type will immediately appear in the Add-Server dropdown
    because VALID_HV_TYPES in main.py is derived from REGISTRY.keys().
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import HypervisorInterface
from .esxi import ESXiAdapter
from .kvm import KVMAdapter
from .hyperv import HyperVAdapter

# ── Optional adapters — uncomment to enable ───────────────────────────────────
# from .proxmox import ProxmoxAdapter   # requires: pip install proxmoxer requests

if TYPE_CHECKING:
    import models

# ── Adapter registry ─────────────────────────────────────────────────────────
# Map hypervisor_type string (as stored in server_config.hypervisor_type)
# to its concrete adapter class.  This is the ONLY place you touch when
# adding a new hypervisor.
REGISTRY: dict[str, type[HypervisorInterface]] = {
    "VMware ESXi": ESXiAdapter,
    "Ubuntu KVM":  KVMAdapter,
    "Hyper-V":     HyperVAdapter,
    # "Proxmox VE":  ProxmoxAdapter,   # uncomment after pip install proxmoxer
}


def get_adapter(row: "models.ServerConfig",
                username: str,
                password: str) -> HypervisorInterface:
    """
    Factory: return the correct adapter instance for a ServerConfig row.

    Raises ValueError for unknown hypervisor_type so the caller can surface
    a meaningful error without an import traceback.
    """
    cls = REGISTRY.get(row.hypervisor_type)
    if cls is None:
        raise ValueError(
            f"No adapter registered for hypervisor_type='{row.hypervisor_type}'. "
            f"Known types: {list(REGISTRY)}"
        )
    return cls(row=row, username=username, password=password)


__all__ = [
    "HypervisorInterface",
    "ESXiAdapter",
    "KVMAdapter",
    "HyperVAdapter",
    "REGISTRY",
    "get_adapter",
]
