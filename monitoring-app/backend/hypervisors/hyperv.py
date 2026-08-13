"""
hypervisors/hyperv.py — Hyper-V adapter (WinRM + PowerShell).

Implements HypervisorInterface for Windows Hyper-V hosts.
Transport: WinRM HTTP port 5985 (basic auth, pywinrm).

Prerequisites on the Windows host (run once as Administrator):
  Enable-PSRemoting -Force
  Set-Item WSMan:\\localhost\\Service\\Auth\\Basic $true
  Set-Item WSMan:\\localhost\\Service\\AllowUnencrypted $true
"""

from __future__ import annotations

import json
from typing import Any

from .base import HypervisorInterface


class HyperVAdapter(HypervisorInterface):
    """Live metrics from a Hyper-V host via WinRM + PowerShell."""

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
        List snapshots via Get-VMSnapshot.
        Returns normalized SnapshotDicts with size_bytes from snapshot disk files.
        """
        try:
            ps = self._ps_session()
            raw = ps(
                f"Get-VMSnapshot -VMName '{vm_name}' | "
                "ForEach-Object { [PSCustomObject]@{ "
                "  Name       = $_.Name; "
                "  CreationTime = $_.CreationTime.ToUniversalTime().ToString('o'); "
                "  SizeGB     = [math]::Round( "
                "    (Get-ChildItem ($_.Path) -Recurse -ErrorAction SilentlyContinue "
                "     | Measure-Object -Property Length -Sum).Sum / 1GB, 3) "
                "} } | ConvertTo-Json -Compress"
            )
            items = json.loads(raw) if raw else []
            if isinstance(items, dict):
                items = [items]
            snaps: list[dict[str, Any]] = []
            for item in items:
                size_bytes = int(float(item.get("SizeGB", 0)) * (1024 ** 3))
                snaps.append({
                    "vm_name":    vm_name,
                    "snap_name":  item.get("Name", ""),
                    "created_at": item.get("CreationTime", ""),
                    "size_bytes": size_bytes,
                })
            return snaps
        except Exception:
            return []

    # ── Private helpers ───────────────────────────────────────────────────────

    def _ps_session(self):
        """Return a callable ps(script) → str backed by a WinRM session."""
        import winrm
        session = winrm.Session(
            f"http://{self.ip}:5985/wsman",
            auth=(self.username, self.password),
            transport="basic",
            server_cert_validation="ignore",
            read_timeout_sec=45,
            operation_timeout_sec=40,
        )

        def ps(script: str) -> str:
            r = session.run_ps(script)
            if r.status_code != 0:
                raise RuntimeError(r.std_err.decode(errors="replace").strip())
            return r.std_out.decode(errors="replace").strip()

        return ps

    def _fetch(self) -> dict[str, Any]:
        ps = self._ps_session()

        # ── CPU ────────────────────────────────────────────────────────────────
        cpu_pct_str = ps(
            "(Get-CimInstance Win32_Processor | "
            "Measure-Object -Property LoadPercentage -Average).Average"
        )
        cpu_pct = round(float(cpu_pct_str or 0), 1)

        cpu_cores_str = ps(
            "(Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors"
        )
        cpu_cores = int(cpu_cores_str or self.row.cpu_cores or 1)

        # ── RAM ────────────────────────────────────────────────────────────────
        ram_json = ps(
            "$cs = Get-CimInstance Win32_OperatingSystem; "
            "[PSCustomObject]@{ "
            "  TotalGB = [math]::Round($cs.TotalVisibleMemorySize / 1MB, 1); "
            "  FreeGB  = [math]::Round($cs.FreePhysicalMemory      / 1MB, 1) "
            "} | ConvertTo-Json"
        )
        ram_data     = json.loads(ram_json)
        ram_total_gb = float(ram_data["TotalGB"])
        ram_free_gb  = float(ram_data["FreeGB"])
        ram_used_gb  = round(ram_total_gb - ram_free_gb, 1)
        ram_pct      = round(ram_used_gb / max(ram_total_gb, 0.1) * 100, 1)

        # ── Drives ────────────────────────────────────────────────────────────
        drives_json = ps(
            "Get-CimInstance Win32_LogicalDisk | "
            "Where-Object { $_.DriveType -eq 3 } | "
            "ForEach-Object { [PSCustomObject]@{ "
            "  Name    = $_.DeviceID; "
            "  TotalGB = [math]::Round($_.Size      / 1GB, 2); "
            "  FreeGB  = [math]::Round($_.FreeSpace / 1GB, 2); "
            "  UsedGB  = [math]::Round(($_.Size - $_.FreeSpace) / 1GB, 2) "
            "} } | ConvertTo-Json -Compress"
        )
        raw_drives = json.loads(drives_json) if drives_json else []
        if isinstance(raw_drives, dict):
            raw_drives = [raw_drives]

        drives: list[dict] = []
        total_cap_gb = total_used_gb = 0.0
        for d in raw_drives:
            t   = float(d.get("TotalGB", 0))
            u   = float(d.get("UsedGB",  0))
            f   = float(d.get("FreeGB",  0))
            pct = round(u / max(t, 0.01) * 100, 1)
            drives.append({
                "name": d.get("Name", "?"), "total_gb": t,
                "used_gb": u, "free_gb": f, "usage_pct": pct,
            })
            total_cap_gb  += t
            total_used_gb += u

        storage_total_tb = round(total_cap_gb  / 1024, 3)
        storage_used_tb  = round(total_used_gb / 1024, 3)
        storage_pct      = round(
            storage_used_tb / max(storage_total_tb, 0.001) * 100, 1
        )

        # ── VMs ───────────────────────────────────────────────────────────────
        vm_json = ps(
            "Get-VM | ForEach-Object { "
            "  $vm = $_; $mem = $vm | Get-VMMemory; $cpu = $vm | Get-VMProcessor; "
            "  $ip = ($vm | Get-VMNetworkAdapter).IPAddresses | "
            "         Where-Object { $_ -match '^\\d+\\.\\d+' } | Select-Object -First 1; "
            "  [PSCustomObject]@{ "
            "    Name        = $vm.Name; "
            "    State       = $vm.State.ToString(); "
            "    CPUUsage    = $vm.CPUUsage; "
            "    vCPUs       = $cpu.Count; "
            "    MemAssigned = [math]::Round($vm.MemoryAssigned / 1GB, 2); "
            "    MemDemand   = [math]::Round($vm.MemoryDemand   / 1GB, 2); "
            "    IPAddress   = if ($ip) { $ip } else { '' } "
            "  } "
            "} | ConvertTo-Json -Compress"
        )
        raw_vms = json.loads(vm_json) if vm_json else []
        if isinstance(raw_vms, dict):
            raw_vms = [raw_vms]

        vms: list[dict] = []
        for v in raw_vms:
            state = str(v.get("State", "")).lower()
            power_state = (
                "running" if "running" in state
                else "paused"  if "paused" in state or "saved" in state
                else "stopped"
            )
            vms.append({
                "vm_name":     v.get("Name", ""),
                "ip_address":  v.get("IPAddress", ""),
                "power_state": power_state,
                "cpu_cores":   int(v.get("vCPUs", 1)),
                "cpu_pct":     float(v.get("CPUUsage", 0)),
                "ram_total_gb": float(v.get("MemAssigned", 0)),
                "ram_used_gb":  float(v.get("MemDemand",   0)),
            })

        return {
            "server_id":         self.row.server_id,
            "hostname":          self.row.hostname or self.ip,
            "display_name":      self.row.display_name,
            "hypervisor_type":   "Hyper-V",
            "ip_address":        self.ip,
            "cpu_usage_pct":     cpu_pct,
            "cpu_cores":         cpu_cores,
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
