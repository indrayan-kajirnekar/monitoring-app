"""
hypervisors/proxmox.py — Proxmox VE adapter (Proxmox REST API v2).

Implements HypervisorInterface for Proxmox VE nodes / clusters.
Transport: HTTPS port 8006 (Proxmox REST API).

Prerequisites on the Proxmox host:
  1. Create a dedicated API user:
       pveum user add hypermonitor@pve --password <password>
       pveum aclmod / -user hypermonitor@pve -role PVEAuditor
  2. Alternatively, use an API token:
       pveum user token add hypermonitor@pve monitor
     Then supply "token" as username and the token secret as password.

This adapter is provided as a ready-to-use plug-in example demonstrating the
Strategy Pattern.  Adding it to production requires only:
  1.  pip install proxmoxer requests  (add to requirements.txt)
  2.  Uncomment the "Proxmox VE" line in hypervisors/__init__.py REGISTRY.
"""

from __future__ import annotations

from typing import Any

from .base import HypervisorInterface


class ProxmoxAdapter(HypervisorInterface):
    """
    Live metrics from a Proxmox VE node via the Proxmox REST API v2.

    Requires: proxmoxer >= 2.0.1  (pip install proxmoxer requests)
    """

    # ── Public interface ──────────────────────────────────────────────────────

    def get_server_status(self) -> dict[str, Any]:
        try:
            return self._fetch()
        except Exception as exc:
            return self._fallback(str(exc))

    def get_all_vms(self) -> list[dict[str, Any]]:
        try:
            return self._fetch().get("vms", [])
        except Exception:
            return []

    def get_vm_snapshots(self, vm_name: str) -> list[dict[str, Any]]:
        """
        Fetch snapshots for a QEMU VM via the Proxmox API.
        Returns normalized SnapshotDicts.
        """
        try:
            px = self._connect()
            # Find the VM by name across all nodes
            for node in px.nodes.get():
                for vm in px.nodes(node["node"]).qemu.get():
                    if vm.get("name") == vm_name:
                        snaps_raw = px.nodes(node["node"]).qemu(vm["vmid"]).snapshot.get()
                        snaps: list[dict[str, Any]] = []
                        for s in snaps_raw:
                            if s.get("name") == "current":
                                continue   # skip the live "current" pseudo-snapshot
                            snaps.append({
                                "vm_name":    vm_name,
                                "snap_name":  s.get("name", ""),
                                "created_at": (
                                    # Proxmox returns epoch seconds in "snaptime"
                                    _epoch_to_iso(s["snaptime"])
                                    if s.get("snaptime") else ""
                                ),
                                "size_bytes": 0,   # API does not expose snap disk size
                            })
                        return snaps
            return []
        except Exception:
            return []

    # ── Private helpers ───────────────────────────────────────────────────────

    def _connect(self):
        """Return an authenticated ProxmoxAPI session."""
        from proxmoxer import ProxmoxAPI   # type: ignore[import]
        return ProxmoxAPI(
            self.ip, user=self.username, password=self.password,
            verify_ssl=False, timeout=30,
        )

    def _fetch(self) -> dict[str, Any]:
        px = self._connect()

        # Aggregate metrics across all cluster nodes
        total_cpu_cores = 0
        cpu_used_sum    = 0.0
        ram_used_bytes  = 0
        ram_total_bytes = 0
        drives: list[dict] = []
        vms: list[dict] = []

        for node in px.nodes.get():
            nname = node["node"]
            nstatus = px.nodes(nname).status.get()

            cores = int(nstatus.get("cpuinfo", {}).get("cpus", 1))
            total_cpu_cores += cores
            cpu_used_sum    += float(nstatus.get("cpu", 0)) * cores  # weighted

            mem = nstatus.get("memory", {})
            ram_total_bytes += int(mem.get("total", 0))
            ram_used_bytes  += int(mem.get("used",  0))

            # Per-node storage
            for stor in px.nodes(nname).storage.get():
                cap  = int(stor.get("total",   0))
                used = int(stor.get("used",    0))
                avail = int(stor.get("avail",  0))
                if cap == 0:
                    continue
                gb = lambda b: round(b / (1024 ** 3), 2)  # noqa: E731
                drives.append({
                    "name":      f"{nname}/{stor['storage']}",
                    "total_gb":  gb(cap),
                    "used_gb":   gb(used),
                    "free_gb":   gb(avail),
                    "usage_pct": round(used / cap * 100, 1),
                })

            # VMs on this node
            for vm in px.nodes(nname).qemu.get():
                pmap = {"running": "running", "stopped": "stopped", "paused": "paused"}
                vms.append({
                    "vm_name":     vm.get("name", str(vm.get("vmid", ""))),
                    "ip_address":  "",   # IP not available without agent
                    "power_state": pmap.get(vm.get("status", ""), "unknown"),
                    "cpu_cores":   int(vm.get("cpus", 1)),
                    "cpu_pct":     round(float(vm.get("cpu", 0)) * 100, 1),
                    "ram_total_gb": round(int(vm.get("maxmem", 0)) / (1024 ** 3), 2),
                    "ram_used_gb":  round(int(vm.get("mem",    0)) / (1024 ** 3), 2),
                })

        cpu_pct    = round(cpu_used_sum / max(total_cpu_cores, 1) * 100, 1)
        ram_total  = round(ram_total_bytes / (1024 ** 3), 1)
        ram_used   = round(ram_used_bytes  / (1024 ** 3), 1)
        ram_pct    = round(ram_used / max(ram_total, 0.1) * 100, 1)
        stor_total = round(sum(d["total_gb"] for d in drives) / 1024, 3)
        stor_used  = round(sum(d["used_gb"]  for d in drives) / 1024, 3)
        stor_pct   = round(stor_used / max(stor_total, 0.001) * 100, 1)

        return {
            "server_id":         self.row.server_id,
            "hostname":          self.row.hostname or self.ip,
            "display_name":      self.row.display_name,
            "hypervisor_type":   "Proxmox VE",
            "ip_address":        self.ip,
            "cpu_usage_pct":     cpu_pct,
            "cpu_cores":         total_cpu_cores,
            "ram_used_gb":       ram_used,
            "ram_total_gb":      ram_total,
            "ram_usage_pct":     ram_pct,
            "storage_used_tb":   stor_used,
            "storage_total_tb":  stor_total,
            "storage_usage_pct": stor_pct,
            "drives":            drives,
            "vm_count":          len(vms),
            "vms":               vms,
            "status":            self.status_from_cpu(cpu_pct),
            "error":             None,
        }


def _epoch_to_iso(epoch: int | float) -> str:
    """Convert a Unix timestamp to an ISO-8601 UTC string."""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(int(epoch), tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )
