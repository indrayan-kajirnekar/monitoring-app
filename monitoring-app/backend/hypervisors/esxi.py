"""
hypervisors/esxi.py — VMware ESXi / vCenter adapter (pyVmomi vSphere API).

Implements HypervisorInterface for ESXi hosts.
Transport: HTTPS port 443 (pyVmomi SmartConnect).
"""

from __future__ import annotations

import ssl
from typing import Any

from .base import HypervisorInterface


class ESXiAdapter(HypervisorInterface):
    """Live metrics from a VMware ESXi / vCenter host via pyVmomi."""

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
        Walk the snapshot tree of the named VM using pyVmomi.
        Returns normalized SnapshotDicts (size_bytes = 0 when unavailable).
        """
        try:
            from pyVmomi import vim
            si, _ = self._connect()
            content = si.RetrieveContent()
            vm_view = content.viewManager.CreateContainerView(
                content.rootFolder, [vim.VirtualMachine], True
            )
            target_vm = next(
                (v for v in vm_view.view
                 if v.config and v.config.name == vm_name),
                None,
            )
            vm_view.Destroy()

            snaps: list[dict[str, Any]] = []
            if target_vm and target_vm.snapshot:
                self._walk_snapshot_tree(
                    target_vm.snapshot.rootSnapshotList, vm_name, snaps
                )
            from pyVim.connect import Disconnect
            Disconnect(si)
            return snaps
        except Exception:
            return []

    # ── Private helpers ───────────────────────────────────────────────────────

    def _connect(self):
        from pyVim.connect import SmartConnect
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        si = SmartConnect(
            host=self.ip, user=self.username, pwd=self.password,
            port=443, sslContext=ctx,
        )
        return si, si.RetrieveContent()

    def _walk_snapshot_tree(self, snap_list, vm_name: str,
                             result: list[dict]) -> None:
        """Recursively walk pyVmomi snapshot tree into flat list."""
        for snap in snap_list:
            created_at = (
                snap.createTime.strftime("%Y-%m-%dT%H:%M:%S+00:00")
                if snap.createTime else ""
            )
            result.append({
                "vm_name":    vm_name,
                "snap_name":  snap.name,
                "created_at": created_at,
                "size_bytes": 0,
            })
            if snap.childSnapshotList:
                self._walk_snapshot_tree(
                    snap.childSnapshotList, vm_name, result
                )

    def _fetch(self) -> dict[str, Any]:
        from pyVmomi import vim
        from pyVim.connect import Disconnect

        si, content = self._connect()

        # ── Scope to the single host matching self.ip ─────────────────────────
        # When connected to a vCenter that manages multiple ESXi hosts, the
        # container view would return ALL hosts and ALL VMs in the cluster.
        # Two ServerConfig entries (e.g. ESXi01 and ESXi02) would each report
        # the full inventory → every VM duplicated N times in /api/vms.
        # Fix: find the one HostSystem whose management IP matches self.ip and
        # scope all subsequent queries to that host only.
        host_view = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.HostSystem], True
        )
        all_hosts = host_view.view
        host_view.Destroy()

        if not all_hosts:
            Disconnect(si)
            raise RuntimeError("No ESXi hosts found in inventory.")

        # Match by management IP (summary.managementServerIp or config.network
        # addresses). Fall back to using ALL hosts if no match (standalone ESXi).
        def _host_ips(h) -> set[str]:
            ips: set[str] = set()
            try:
                for nic in h.config.network.vnic:
                    ips.add(nic.spec.ip.ipAddress)
            except Exception:
                pass
            try:
                ips.add(h.summary.managementServerIp or "")
            except Exception:
                pass
            try:
                ips.add(h.name)   # hostname or IP as registered in vCenter
            except Exception:
                pass
            return ips

        matched = [h for h in all_hosts if self.ip in _host_ips(h)]
        hosts = matched if matched else all_hosts  # fallback: standalone ESXi

        # ── Host aggregate metrics (scoped to this host only) ─────────────────
        total_ram_bytes = sum(h.hardware.memorySize for h in hosts)
        total_cpu_cores = sum(h.hardware.cpuInfo.numCpuCores for h in hosts)
        cpu_used_mhz    = sum(
            h.summary.quickStats.overallCpuUsage or 0 for h in hosts
        )
        cpu_total_mhz   = sum(
            h.hardware.cpuInfo.numCpuCores * h.hardware.cpuInfo.hz / 1e6
            for h in hosts
        )
        cpu_pct = round(cpu_used_mhz / max(cpu_total_mhz, 1) * 100, 1)

        ram_used_bytes = (
            sum(h.summary.quickStats.overallMemoryUsage or 0 for h in hosts)
            * 1024 * 1024
        )
        ram_total_gb = round(total_ram_bytes / (1024 ** 3), 1)
        ram_used_gb  = round(ram_used_bytes  / (1024 ** 3), 1)
        ram_pct      = round(ram_used_gb / max(ram_total_gb, 0.1) * 100, 1)

        # ── Datastores — only datastores mounted on this host ─────────────────
        # Collect the set of datastores accessible from the matched host(s) so
        # shared datastores are not double-counted across two ESXi entries.
        seen: set[str] = set()
        drives: list[dict] = []
        total_cap_bytes = total_free_bytes = 0

        host_datastores: list = []
        for h in hosts:
            try:
                host_datastores.extend(h.datastore)
            except Exception:
                pass

        for ds in host_datastores:
            if ds.name in seen:
                continue
            seen.add(ds.name)
            cap  = ds.summary.capacity  or 0
            free = ds.summary.freeSpace or 0
            used = cap - free
            if cap == 0:
                continue
            total_cap_bytes  += cap
            total_free_bytes += free
            sz_gb  = round(cap  / (1024 ** 3), 2)
            us_gb  = round(used / (1024 ** 3), 2)
            fr_gb  = round(free / (1024 ** 3), 2)
            drives.append({
                "name": ds.name, "total_gb": sz_gb,
                "used_gb": us_gb, "free_gb": fr_gb,
                "usage_pct": round(us_gb / max(sz_gb, 0.01) * 100, 1),
            })

        storage_total_tb = round(total_cap_bytes                           / (1024 ** 4), 3)
        storage_used_tb  = round((total_cap_bytes - total_free_bytes)      / (1024 ** 4), 3)
        storage_pct      = round(storage_used_tb / max(storage_total_tb, 0.001) * 100, 1)

        # ── VM inventory — only VMs whose runtime.host is one of our hosts ────
        # This is the critical filter: without it every vCenter-connected ESXi
        # entry returns the FULL cluster inventory, causing N-fold duplicates.
        host_refs = {h._moId for h in hosts}

        vm_view = content.viewManager.CreateContainerView(
            content.rootFolder, [vim.VirtualMachine], True
        )
        all_vms_raw = vm_view.view
        vm_view.Destroy()

        vms: list[dict] = []
        for vm in all_vms_raw:
            cfg = vm.config
            if cfg is None:
                continue
            # Skip VMs not running on this host
            try:
                if vm.runtime.host._moId not in host_refs:
                    continue
            except Exception:
                pass
            qs  = vm.summary.quickStats
            gs  = vm.guest
            p_map = {
                "poweredOn": "running", "poweredOff": "stopped", "suspended": "paused",
            }
            power_state  = p_map.get(vm.runtime.powerState, "unknown")
            vm_alloc_mhz = cfg.hardware.numCPU * (
                cpu_total_mhz / max(total_cpu_cores, 1)
            )
            vm_cpu_pct   = round(
                min((qs.overallCpuUsage or 0) / max(vm_alloc_mhz, 1) * 100, 100.0), 1
            )
            vm_ip = gs.ipAddress if gs and gs.ipAddress else ""
            vms.append({
                "vm_name":     cfg.name,
                "ip_address":  vm_ip,
                "power_state": power_state,
                "cpu_cores":   cfg.hardware.numCPU,
                "cpu_pct":     vm_cpu_pct,
                "ram_used_gb":  round(
                    (qs.guestMemoryUsage or qs.balloonedMemory or 0) / 1024, 2),
                "ram_total_gb": round(cfg.hardware.memoryMB / 1024, 2),
            })

        Disconnect(si)

        return {
            "server_id":         self.row.server_id,
            "hostname":          self.row.hostname or self.ip,
            "display_name":      self.row.display_name,
            "hypervisor_type":   "VMware ESXi",
            "ip_address":        self.ip,
            "cpu_usage_pct":     cpu_pct,
            "cpu_cores":         total_cpu_cores,
            "ram_used_gb":       ram_used_gb,
            "ram_total_gb":      ram_total_gb,
            "ram_usage_pct":     ram_pct,
            "storage_used_tb":   storage_used_tb,
            "storage_total_tb":  storage_total_tb,
            "storage_usage_pct": storage_pct,
            "drives":            drives,
            "vm_count":          len(vms),
            "vms":               vms,
            "status":            self.status_from_cpu(cpu_pct),
            "error":             None,
        }
