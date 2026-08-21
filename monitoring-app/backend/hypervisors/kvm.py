"""
hypervisors/kvm.py — KVM/Linux adapter (SSH + virsh + /proc).

Implements HypervisorInterface for Ubuntu KVM hosts.
Transport: paramiko SSH, port 22.
"""

from __future__ import annotations

import time
from typing import Any

from .base import HypervisorInterface


class KVMAdapter(HypervisorInterface):
    """Live metrics from a KVM/Linux host via SSH."""

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
        List snapshots for a VM via virsh snapshot-list.
        Returns normalized SnapshotDicts.
        """
        try:
            client = self._connect()
            # Do NOT use --no-metadata: that flag skips internal snapshots
            # (the default type on most KVM hosts), leaving the list empty.
            # Omitting it returns all snapshot types (internal + external).
            out = self._run(
                client,
                f"virsh -c qemu:///system snapshot-list '{vm_name}' 2>/dev/null"
                " || virsh snapshot-list '{vm_name}' 2>/dev/null || echo ''",
            )
            client.close()
            snaps: list[dict[str, Any]] = []
            for line in out.splitlines():
                # Header lines start with " Name" or "---"; skip them
                stripped = line.strip()
                if not stripped or stripped.startswith("Name") or stripped.startswith("-"):
                    continue
                parts = stripped.split()
                if len(parts) < 3:
                    continue
                snap_name   = parts[0]
                created_str = f"{parts[1]}T{parts[2]}+00:00"
                snaps.append({
                    "vm_name":    vm_name,
                    "snap_name":  snap_name,
                    "created_at": created_str,
                    "size_bytes": 0,   # virsh snapshot-list doesn't expose size
                })
            return snaps
        except Exception:
            return []

    # ── Private helpers ───────────────────────────────────────────────────────

    def _connect(self):
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=self.ip,
            username=self.username,
            password=self.password,
            timeout=15, banner_timeout=20,
            allow_agent=False, look_for_keys=False,
        )
        return client

    @staticmethod
    def _run(client, cmd: str) -> str:
        _, stdout, _ = client.exec_command(cmd, timeout=15)
        return stdout.read().decode(errors="replace").strip()

    def _fetch(self) -> dict[str, Any]:
        client = self._connect()
        run    = lambda cmd: self._run(client, cmd)  # noqa: E731

        # ── CPU utilisation (1-second /proc/stat sample) ──────────────────────
        cpu_line1 = run("grep -m1 '^cpu ' /proc/stat")
        run("sleep 1")
        cpu_line2 = run("grep -m1 '^cpu ' /proc/stat")

        def _parse_cpu(line: str):
            vals  = list(map(int, line.split()[1:]))
            idle  = vals[3] + (vals[4] if len(vals) > 4 else 0)
            total = sum(vals)
            return idle, total

        idle1, total1 = _parse_cpu(cpu_line1)
        idle2, total2 = _parse_cpu(cpu_line2)
        delta_total   = total2 - total1
        delta_idle    = idle2  - idle1
        cpu_pct       = round((1 - delta_idle / max(delta_total, 1)) * 100, 1)

        cpu_cores = int(run("nproc") or self.row.cpu_cores or 1)

        # ── RAM ───────────────────────────────────────────────────────────────
        mem_info = run("grep -E '^(MemTotal|MemAvailable):' /proc/meminfo")
        mem_dict: dict[str, int] = {}
        for line in mem_info.splitlines():
            k, v = line.split(":")
            mem_dict[k.strip()] = int(v.strip().split()[0])

        ram_total_gb = round(mem_dict.get("MemTotal", 0) / (1024 ** 2), 1)
        ram_free_gb  = round(mem_dict.get("MemAvailable", 0) / (1024 ** 2), 1)
        ram_used_gb  = round(ram_total_gb - ram_free_gb, 1)
        ram_pct      = round(ram_used_gb / max(ram_total_gb, 0.1) * 100, 1)

        # ── Per-volume drive breakdown ─────────────────────────────────────────
        df_out = run(
            "df -B 1 --output=target,size,used,avail "
            "-x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null | tail -n +2"
        )
        drives: list[dict] = []
        total_used_bytes = total_size_bytes = 0
        for line in df_out.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            mount, size_b, used_b, avail_b = (
                parts[0], int(parts[1]), int(parts[2]), int(parts[3])
            )
            if size_b < 100 * 1024 * 1024:
                continue
            size_gb = round(size_b  / (1024 ** 3), 2)
            used_gb = round(used_b  / (1024 ** 3), 2)
            free_gb = round(avail_b / (1024 ** 3), 2)
            pct     = round(used_gb / max(size_gb, 0.01) * 100, 1)
            drives.append({
                "name": mount, "total_gb": size_gb,
                "used_gb": used_gb, "free_gb": free_gb, "usage_pct": pct,
            })
            total_used_bytes += used_b
            total_size_bytes += size_b

        storage_used_tb  = round(total_used_bytes / (1024 ** 4), 3)
        storage_total_tb = round(total_size_bytes / (1024 ** 4), 3)
        storage_pct      = round(
            storage_used_tb / max(storage_total_tb, 0.001) * 100, 1
        )

        # ── VM inventory via virsh ────────────────────────────────────────────
        virsh_out = run(
            "virsh -c qemu:///system list --all --name 2>/dev/null || "
            "virsh list --all --name 2>/dev/null || echo ''"
        )
        vm_names = [v.strip() for v in virsh_out.splitlines() if v.strip()]

        # Two cpu.time samples for real % calc
        def _bulk_domstats() -> dict[str, int]:
            out = run(
                "virsh -c qemu:///system domstats --cpu-total --raw 2>/dev/null || "
                "virsh domstats --cpu-total --raw 2>/dev/null || echo ''"
            )
            result: dict[str, int] = {}
            current: str | None = None
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("Domain:"):
                    current = line.split("'")[-2] if "'" in line else line.split()[-1]
                elif current and "cpu.time=" in line:
                    try:
                        result[current] = int(line.split("=")[1])
                    except (ValueError, IndexError):
                        pass
            return result

        stats1  = _bulk_domstats()
        t_start = time.monotonic()
        run("sleep 1")
        stats2      = _bulk_domstats()
        elapsed_ns  = (time.monotonic() - t_start) * 1e9

        vms: list[dict] = []
        for vm_name in vm_names:
            state_out = run(
                f"virsh -c qemu:///system domstate '{vm_name}' 2>/dev/null || echo unknown"
            )
            state = state_out.strip().lower()
            power_state = (
                "running" if "running" in state
                else "paused"  if "paused"  in state
                else "stopped"
            )
            dominfo = run(
                f"virsh -c qemu:///system dominfo '{vm_name}' 2>/dev/null || echo ''"
            )
            vcpus = 1
            vm_ram_used = vm_ram_total = 0.0
            for dline in dominfo.splitlines():
                if dline.startswith("CPU(s):"):
                    try: vcpus = int(dline.split(":")[1].strip())
                    except ValueError: pass
                elif dline.startswith("Used memory:"):
                    try: vm_ram_used = round(
                        int(dline.split(":")[1].strip().split()[0]) / (1024 ** 2), 2)
                    except ValueError: pass
                elif dline.startswith("Max memory:"):
                    try: vm_ram_total = round(
                        int(dline.split(":")[1].strip().split()[0]) / (1024 ** 2), 2)
                    except ValueError: pass

            vm_cpu_pct = 0.0
            if power_state == "running":
                t1, t2 = stats1.get(vm_name), stats2.get(vm_name)
                if t1 is not None and t2 is not None and elapsed_ns > 0:
                    vm_cpu_pct = round(
                        min((t2 - t1) / (elapsed_ns * max(vcpus, 1)) * 100, 100.0), 1
                    )

            vms.append({
                "vm_name":     vm_name,
                "ip_address":  "",
                "power_state": power_state,
                "cpu_cores":   vcpus,
                "cpu_pct":     vm_cpu_pct,
                "ram_used_gb":  vm_ram_used,
                "ram_total_gb": vm_ram_total,
            })

        client.close()

        return {
            "server_id":         self.row.server_id,
            "hostname":          self.row.hostname or self.ip,
            "display_name":      self.row.display_name,
            "hypervisor_type":   "Ubuntu KVM",
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
