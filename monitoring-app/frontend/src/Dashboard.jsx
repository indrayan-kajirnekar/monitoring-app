/**
 * Dashboard.jsx — Live monitoring dashboard  v4.0
 *
 * Architecture changes:
 *   • Centralized reactive filter state object (Req 2)
 *     - Single `filters` object drives ALL list rendering — no fragmented state.
 *     - Level 1 (Hypervisor) and Level 2 (Server) dropdowns are bound to it.
 *     - Changing Level 1 automatically resets Level 2 (cascade reset).
 *   • Client-side VMQueryBuilder mirrors the backend builder for instant feedback (Req 4)
 *     - Auto-detects IP / slug / free-text and applies the correct OR strategy.
 *   • Snapshotable filter state — pass ?hypervisor=X&server=Y in URL? Trivial to add.
 *   • All filter/search logic lives in ONE place: applyFilters().
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import axios from "axios";
import { useAuth } from "./AuthContext";

const API     = process.env.REACT_APP_API_URL || "";
// Poll every 30 s — server cache TTL is now 60 s, so one poll per two cycles
// is enough to stay visually fresh without hammering the backend.
const POLL_MS = 30000;

// ─────────────────────────────────────────────────────────────────────────────
// Req 4 — Client-side VMQueryBuilder
// Mirrors the backend query_builder.py logic for instant, zero-latency filtering.
// The backend endpoint also accepts the same params for server-side pagination
// on large inventories.
// ─────────────────────────────────────────────────────────────────────────────

const _IP_RE   = /^(\d{1,3}\.){1,3}\d{0,3}$|^[\da-fA-F:]{2,39}$/;
const _SLUG_RE = /^[a-z0-9]+-[a-z0-9-]+$/i;

function applyVMFilters(vms, { hypervisorType, serverId, powerState, search }) {
  return vms.filter(vm => {
    // Level 1 — hypervisor type
    if (hypervisorType && vm.hypervisor_type !== hypervisorType) return false;
    // Level 2 — specific server (cascades from Level 1)
    if (serverId      && vm.host_server_id  !== serverId)       return false;
    // Power state filter
    if (powerState && powerState !== "all" && vm.power_state !== powerState) return false;

    // Smart global search (Req 4 Builder Pattern)
    if (search) {
      const q = search.trim();
      if (!q) return true;
      if (_IP_RE.test(q)) {
        // IP or prefix — substring on ip_address
        if (!(vm.ip_address || "").includes(q)) return false;
      } else if (_SLUG_RE.test(q)) {
        // Slug pattern — exact match on host_server_id OR vm_id
        const ql = q.toLowerCase();
        if ((vm.host_server_id || "").toLowerCase() !== ql &&
            (vm.vm_id          || "").toLowerCase() !== ql) return false;
      } else {
        // Free text — OR across name / owner / purpose / IP
        const ql = q.toLowerCase();
        const hit = (
          (vm.vm_name    || "").toLowerCase().includes(ql) ||
          (vm.owner_name || "").toLowerCase().includes(ql) ||
          (vm.purpose    || "").toLowerCase().includes(ql) ||
          (vm.ip_address || "").toLowerCase().includes(ql)
        );
        if (!hit) return false;
      }
    }
    return true;
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Tiny primitives
// ─────────────────────────────────────────────────────────────────────────────

function GaugeBar({ value }) {
  const pct    = Math.min(100, Math.max(0, Math.round(value)));
  const colour = pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-amber-400" : "bg-emerald-500";
  return (
    <div className="w-full bg-slate-200 rounded-full h-2 overflow-hidden">
      <div className={`h-2 rounded-full transition-all duration-700 ${colour}`}
           style={{ width: `${pct}%` }} />
    </div>
  );
}

function HypervisorBadge({ type }) {
  const p = {
    "VMware ESXi": "bg-blue-100 text-blue-800",
    "Ubuntu KVM":  "bg-orange-100 text-orange-800",
    "Hyper-V":     "bg-purple-100 text-purple-800",
  };
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold
      ${p[type] ?? "bg-slate-100 text-slate-700"}`}>
      {type}
    </span>
  );
}

function PowerBadge({ state }) {
  const s = {
    running: "bg-emerald-100 text-emerald-700",
    stopped: "bg-slate-100 text-slate-500",
    paused:  "bg-amber-100 text-amber-700",
    unknown: "bg-slate-100 text-slate-400",
  };
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold capitalize
      ${s[state] ?? s.unknown}`}>
      {state || "unknown"}
    </span>
  );
}

function StatusDot({ status }) {
  const c = {
    online:   "bg-emerald-500",
    warning:  "bg-amber-400",
    critical: "bg-red-500",
    stopped:  "bg-slate-400",
  };
  return (
    <span className="flex items-center gap-1.5">
      <span className={`inline-block w-2.5 h-2.5 rounded-full ${c[status] ?? "bg-slate-400"}`} />
      <span className="capitalize text-sm">{status}</span>
    </span>
  );
}

function StatCard({ label, value, sub, accent }) {
  return (
    <div className="bg-white rounded-xl shadow-sm border border-slate-200 p-5 flex flex-col gap-1">
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">{label}</p>
      <p className={`text-3xl font-bold ${accent ?? "text-slate-800"}`}>{value}</p>
      {sub && <p className="text-xs text-slate-400">{sub}</p>}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Drive breakdown row
// ─────────────────────────────────────────────────────────────────────────────

function DriveRow({ drive }) {
  return (
    <div className="flex flex-col gap-0.5">
      <div className="flex justify-between text-xs text-slate-500">
        <span className="font-mono font-medium text-slate-700">{drive.name}</span>
        <span>
          {drive.used_gb} / {drive.total_gb} GB
          <span className="ml-1 text-slate-400">({drive.usage_pct}%)</span>
        </span>
      </div>
      <GaugeBar value={drive.usage_pct} />
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Snapshot panel — expandable row showing per-VM snapshots (Fix 3)
// ─────────────────────────────────────────────────────────────────────────────

function SnapshotPanel({ vmId, vmName }) {
  const [snaps,   setSnaps]   = useState(null);   // null=not loaded, []=[]=none
  const [loading, setLoading] = useState(false);
  const [error,   setError]   = useState(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const { data } = await axios.get(`${API}/api/vms/${vmId}/snapshots`);
      setSnaps(data);
    } catch (e) {
      setError(e?.response?.data?.detail || "Failed to fetch snapshots.");
    } finally {
      setLoading(false);
    }
  };

  // Auto-load on first mount
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading) return (
    <div className="flex items-center gap-2 py-2 text-xs text-slate-400">
      <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
      </svg>
      Fetching snapshots from hypervisor…
    </div>
  );

  if (error) return (
    <div className="flex items-center gap-2 py-2 text-xs text-red-500">
      ⚠ {error}
      <button onClick={load} className="underline text-blue-500 ml-2">Retry</button>
    </div>
  );

  if (!snaps) return null;

  if (snaps.length === 0) return (
    <p className="py-2 text-xs text-slate-400 italic">No snapshots found for <strong>{vmName}</strong>.</p>
  );

  return (
    <div className="mt-1">
      <table className="text-xs w-full">
        <thead>
          <tr className="text-slate-400 border-b border-slate-200">
            <th className="pb-1 pr-4 font-semibold text-left">Snapshot Name</th>
            <th className="pb-1 pr-4 font-semibold text-left">Created At (UTC)</th>
            <th className="pb-1 font-semibold text-right">Size</th>
          </tr>
        </thead>
        <tbody>
          {snaps.map((s, i) => (
            <tr key={i} className="border-b border-slate-100 last:border-0">
              <td className="py-1 pr-4 font-mono text-slate-700">{s.snap_name || "—"}</td>
              <td className="py-1 pr-4 text-slate-500">
                {s.created_at
                  ? new Date(s.created_at).toLocaleString()
                  : <span className="text-slate-300">—</span>}
              </td>
              <td className="py-1 text-right tabular-nums text-slate-500">
                {s.size_bytes > 0
                  ? s.size_bytes >= 1073741824
                    ? `${(s.size_bytes / 1073741824).toFixed(2)} GB`
                    : `${(s.size_bytes / 1048576).toFixed(0)} MB`
                  : <span className="text-slate-300">—</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button onClick={load}
              className="mt-2 text-[10px] text-blue-400 hover:text-blue-600 underline">
        ↻ Refresh snapshots
      </button>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Per-server card  (selectable with checkbox for export)
// ─────────────────────────────────────────────────────────────────────────────

function ServerCard({ server, selected, onSelect }) {
  const [showDrives, setShowDrives] = useState(false);
  const hasDrives = server.drives && server.drives.length > 0;
  const isStale   = server.cache_age_s != null && server.cache_age_s > 90;

  return (
    <div className={`bg-white rounded-xl shadow-sm border p-5 flex flex-col gap-3
      ${server.status === "critical" ? "border-red-300" : selected ? "border-blue-400 ring-1 ring-blue-300" : "border-slate-200"}`}>

      {/* Header row — checkbox + name + status */}
      <div className="flex items-start gap-2">
        {/* Selection checkbox */}
        <input
          type="checkbox"
          checked={selected}
          onChange={e => onSelect(server.server_id, e.target.checked)}
          className="mt-1 h-3.5 w-3.5 rounded border-slate-300 text-blue-600
            focus:ring-blue-400 cursor-pointer shrink-0"
        />
        <div className="flex-1 min-w-0">
          <p className="font-semibold text-slate-800 text-sm truncate" title={server.display_name}>
            {server.display_name}
          </p>
          <p className="text-xs text-slate-400 font-mono">{server.ip_address}</p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <StatusDot status={server.status} />
          {server.cache_age_s != null && (
            <span className={`text-[10px] tabular-nums ${isStale ? "text-amber-500 font-semibold" : "text-slate-300"}`}>
              {isStale ? "⚠ " : ""}polled {Math.round(server.cache_age_s)}s ago
            </span>
          )}
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <HypervisorBadge type={server.hypervisor_type} />
        {server.cpu_cores > 0 && (
          <span className="text-xs text-slate-500">{server.cpu_cores} CPUs</span>
        )}
        <span className="text-xs text-slate-400 ml-auto">{server.vm_count} VMs</span>
      </div>

      {/* CPU */}
      <div>
        <div className="flex justify-between text-xs text-slate-500 mb-1">
          <span>CPU</span>
          <span className="font-medium text-slate-700">{server.cpu_usage_pct}%</span>
        </div>
        <GaugeBar value={server.cpu_usage_pct} />
      </div>

      {/* RAM */}
      <div>
        <div className="flex justify-between text-xs text-slate-500 mb-1">
          <span>RAM</span>
          <span className="font-medium text-slate-700">
            {server.ram_used_gb} / {server.ram_total_gb} GB
            <span className="ml-1 text-slate-400">({server.ram_usage_pct}%)</span>
          </span>
        </div>
        <GaugeBar value={server.ram_usage_pct} />
      </div>

      {/* Storage — aggregate gauge + optional per-drive breakdown */}
      <div>
        <div className="flex justify-between text-xs text-slate-500 mb-1">
          <span className="flex items-center gap-1">
            Storage
            {hasDrives && (
              <button
                onClick={() => setShowDrives(d => !d)}
                className="text-blue-500 hover:text-blue-700 ml-1 text-[10px] font-semibold">
                {showDrives ? "▲ hide" : `▼ ${server.drives.length} drive${server.drives.length > 1 ? "s" : ""}`}
              </button>
            )}
          </span>
          <span className="font-medium text-slate-700">
            {server.storage_used_tb} / {server.storage_total_tb} TB
            <span className="ml-1 text-slate-400">({server.storage_usage_pct}%)</span>
          </span>
        </div>
        <GaugeBar value={server.storage_usage_pct} />

        {showDrives && hasDrives && (
          <div className="mt-2 flex flex-col gap-1.5 pl-2 border-l-2 border-slate-100">
            {server.drives.map((d, i) => <DriveRow key={i} drive={d} />)}
          </div>
        )}
      </div>

      {server.error && (
        <p className="text-xs text-red-500 bg-red-50 border border-red-100 rounded px-2 py-1 break-words">
          ⚠ {server.error}
        </p>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Presentation CSV builder  (client-side, UTF-8 BOM, Excel-ready)
// ─────────────────────────────────────────────────────────────────────────────

function buildPresentationCsv(servers, vms) {
  const now       = new Date().toUTCString();
  const totalVMs  = vms.length;
  const runningVMs = vms.filter(v => v.power_state === "running").length;
  const critCt    = servers.filter(s => s.status === "critical").length;
  const warnCt    = servers.filter(s => s.status === "warning").length;
  const avgCpu    = servers.length
    ? (servers.reduce((s, h) => s + h.cpu_usage_pct, 0) / servers.length).toFixed(1)
    : 0;

  const esc = v => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const row = cols => cols.map(esc).join(",") + "\n";

  let csv = "";

  // ── Executive summary block ───────────────────────────────────────────────
  csv += "HYPERMONITOR — INFRASTRUCTURE DASHBOARD REPORT\n";
  csv += `Generated:,${now}\n`;
  csv += "\n";
  csv += "EXECUTIVE SUMMARY\n";
  csv += `Total Hypervisor Hosts,${servers.length}\n`;
  csv += `Total Virtual Machines,${totalVMs}\n`;
  csv += `Running VMs,${runningVMs}\n`;
  csv += `Average CPU Utilisation,${avgCpu}%\n`;
  csv += `Hosts in Critical State,${critCt}\n`;
  csv += `Hosts in Warning State,${warnCt}\n`;
  csv += `Hosts Online,${servers.length - critCt - warnCt}\n`;
  csv += "\n";

  // ── Server utilisation table ──────────────────────────────────────────────
  csv += "HOST UTILISATION DETAIL\n";
  csv += row(["Host Name", "IP Address", "Hypervisor", "CPU %", "CPU Cores",
              "RAM Used (GB)", "RAM Total (GB)", "RAM %",
              "Storage Used (TB)", "Storage Total (TB)", "Storage %",
              "VM Count", "Status"]);
  for (const s of servers) {
    csv += row([s.display_name, s.ip_address, s.hypervisor_type,
                s.cpu_usage_pct, s.cpu_cores,
                s.ram_used_gb, s.ram_total_gb, s.ram_usage_pct,
                s.storage_used_tb, s.storage_total_tb, s.storage_usage_pct,
                s.vm_count, (s.status || "").toUpperCase()]);
  }
  csv += "\n";

  // ── VM inventory table ────────────────────────────────────────────────────
  csv += "VM INVENTORY DETAIL\n";
  csv += row(["VM Name", "IP Address", "Hypervisor", "Power State",
              "CPU %", "vCPUs", "RAM Used (GB)", "RAM Total (GB)", "RAM %",
              "Owner", "Creation Date", "Purpose", "Status"]);
  for (const v of vms) {
    csv += row([v.vm_name, v.ip_address, v.hypervisor_type,
                (v.power_state || "").toUpperCase(),
                v.cpu_usage_pct, v.cpu_cores,
                v.ram_used_gb, v.ram_total_gb, v.ram_usage_pct,
                v.owner_name, v.creation_date, v.purpose,
                (v.status || "").toUpperCase()]);
  }

  // UTF-8 BOM so Excel auto-detects encoding
  return "\uFEFF" + csv;
}

function triggerCsvDownload(content, filename) {
  const blob = new Blob([content], { type: "text/csv;charset=utf-8;" });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement("a");
  a.href     = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

// ─────────────────────────────────────────────────────────────────────────────
// VM Inventory Table  — driven by centralized filters prop (Req 2)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * VMTable props
 * ─────────────
 * vms         – full unfiltered VM list from the API
 * filters     – centralized filter state object from Dashboard
 *               { hypervisorType, serverId, powerState, search }
 * onFilters   – setState setter for filters (used for search + power pill)
 * onVmsUpdated – callback after a successful inline metadata save
 */
function VMTable({ vms, filters, onFilters, onVmsUpdated }) {
  const [sortKey,  setSortKey]  = useState("vm_name");
  const [sortAsc,  setSortAsc]  = useState(true);
  // Which vm_ids have their snapshot panel expanded
  const [snapOpen, setSnapOpen] = useState({});
  const toggleSnap = (vmId) => setSnapOpen(s => ({ ...s, [vmId]: !s[vmId] }));

  // Inline edit state: { [vm_id]: { owner_name, purpose } }
  const [edits,  setEdits]  = useState({});
  const [saving, setSaving] = useState({});

  const isDirty = vm =>
    edits[vm.vm_id] &&
    (edits[vm.vm_id].owner_name !== vm.owner_name ||
     edits[vm.vm_id].purpose    !== vm.purpose);

  const getEdit = (vm, field) =>
    edits[vm.vm_id] ? edits[vm.vm_id][field] : vm[field];

  const setEdit = (vm, field, value) =>
    setEdits(e => ({
      ...e,
      [vm.vm_id]: {
        owner_name: e[vm.vm_id]?.owner_name ?? vm.owner_name,
        purpose:    e[vm.vm_id]?.purpose    ?? vm.purpose,
        [field]: value,
      },
    }));

  const saveRow = async (vm) => {
    if (!isDirty(vm)) return;
    setSaving(s => ({ ...s, [vm.vm_id]: true }));
    try {
      await axios.put(`${API}/api/vms/metadata/${vm.vm_id}`, {
        vm_id:           vm.vm_id,
        vm_name:         vm.vm_name,
        ip_address:      vm.ip_address,
        hypervisor_type: vm.hypervisor_type,
        owner_name:      edits[vm.vm_id]?.owner_name ?? vm.owner_name,
        creation_date:   vm.creation_date,
        purpose:         edits[vm.vm_id]?.purpose    ?? vm.purpose,
      });
      setEdits(e => { const n = { ...e }; delete n[vm.vm_id]; return n; });
      onVmsUpdated?.();
    } catch { /* leave edits so user can retry */ }
    finally { setSaving(s => { const n = { ...s }; delete n[vm.vm_id]; return n; }); }
  };

  const handleSort = (key) => {
    if (key === sortKey) setSortAsc(a => !a);
    else { setSortKey(key); setSortAsc(true); }
  };

  // ── Apply centralized filters via applyVMFilters (Req 2 + 4) ────────────────
  const displayed = useMemo(() =>
    applyVMFilters(vms, filters)
      .sort((a, b) => {
        const av = a[sortKey], bv = b[sortKey];
        const cmp = typeof av === "number" ? av - bv : String(av).localeCompare(String(bv));
        return sortAsc ? cmp : -cmp;
      }),
    [vms, filters, sortKey, sortAsc]
  );

  const running    = vms.filter(v => v.power_state === "running").length;
  const stopped    = vms.filter(v => v.power_state === "stopped").length;
  const dirtyCount = Object.keys(edits).length;

  const Th = ({ label, col }) => (
    <th onClick={() => handleSort(col)}
        className="px-3 py-3 text-left text-xs font-semibold uppercase tracking-wider
          text-slate-500 cursor-pointer select-none whitespace-nowrap hover:text-slate-800">
      {label}
      {sortKey === col && <span className="ml-1 text-blue-500">{sortAsc ? "▲" : "▼"}</span>}
    </th>
  );

  return (
    <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
      {/* Toolbar — all filter controls bound to centralized `filters` state (Req 2) */}
      <div className="px-5 py-4 border-b border-slate-100 flex flex-col sm:flex-row gap-3
                      items-start sm:items-center justify-between">
        <div className="flex items-center gap-3 flex-wrap">
          <h2 className="font-semibold text-slate-800">VM Inventory</h2>
          <span className="text-xs text-slate-400">
            {running} running · {stopped} stopped · {vms.length} total
          </span>
          {/* Power state filter pills — write directly into centralized filters */}
          <div className="flex gap-1">
            {["all", "running", "stopped", "paused"].map(p => (
              <button key={p}
                      onClick={() => onFilters(f => ({ ...f, powerState: p }))}
                      className={`text-xs px-2 py-0.5 rounded-full font-medium capitalize
                        ${(filters.powerState || "all") === p
                          ? "bg-slate-700 text-white"
                          : "bg-slate-100 text-slate-500 hover:bg-slate-200"}`}>
                {p}
              </button>
            ))}
          </div>
          {dirtyCount > 0 && (
            <span className="text-xs bg-amber-100 text-amber-700 border border-amber-300
              px-2 py-0.5 rounded-full font-semibold animate-pulse">
              {dirtyCount} unsaved change{dirtyCount > 1 ? "s" : ""}
            </span>
          )}
        </div>
        <div className="flex gap-2 items-center flex-wrap">
          {/* Search — auto-detects IP / slug / free-text via applyVMFilters (Req 4) */}
          <input type="text"
                 placeholder="Search VMs, IPs, owners… (auto-detects IP / owner / slug)"
                 value={filters.search || ""}
                 onChange={e => onFilters(f => ({ ...f, search: e.target.value }))}
                 className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm w-full sm:w-72
                   focus:outline-none focus:ring-2 focus:ring-blue-400" />
          {filters.search && (
            <button onClick={() => onFilters(f => ({ ...f, search: "" }))}
                    className="text-xs text-slate-400 hover:text-slate-700 underline whitespace-nowrap">
              ✕ clear
            </button>
          )}
        </div>
      </div>

      {/* Inline-edit hint banner */}
      <div className="px-5 py-2 bg-blue-50 border-b border-blue-100 flex items-center gap-2 text-xs text-blue-700">
        <svg className="w-3.5 h-3.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round"
                d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/>
        </svg>
        Click any <strong>Owner</strong> or <strong>Purpose</strong> cell to edit inline, then press
        <kbd className="mx-1 px-1 py-0.5 bg-blue-100 rounded font-mono text-[10px]">Enter</kbd>
        or click <strong>Save</strong> to persist.
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-slate-100">
          <thead className="bg-slate-50">
            <tr>
              <Th label="VM Name"    col="vm_name" />
              <Th label="IP"         col="ip_address" />
              <Th label="Hypervisor" col="hypervisor_type" />
              <Th label="Power"      col="power_state" />
              <Th label="CPU %"      col="cpu_usage_pct" />
              <Th label="vCPUs"      col="cpu_cores" />
              <Th label="RAM Used"   col="ram_used_gb" />
              <Th label="RAM Total"  col="ram_total_gb" />
              {/* Owner / Purpose are editable — shown with pencil icon */}
              <th className="px-3 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500 whitespace-nowrap">
                Owner
                <span className="ml-1 text-slate-300">✎</span>
              </th>
              <Th label="Created"    col="creation_date" />
              <th className="px-3 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500 whitespace-nowrap">
                Purpose
                <span className="ml-1 text-slate-300">✎</span>
              </th>
              <Th label="Status"     col="status" />
              {/* Snapshots expand toggle */}
              <th className="px-3 py-3 w-20 text-xs font-semibold uppercase tracking-wider text-slate-500">
                Snaps
              </th>
              {/* Save column — only visible when a row is dirty */}
              <th className="px-3 py-3 w-16" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-50">
            {displayed.length === 0 ? (
              <tr>
                <td colSpan={13} className="text-center py-12 text-slate-400 text-sm">
                  No VMs match your filter.
                </td>
              </tr>
            ) : displayed.map(vm => {
              const dirty = isDirty(vm);
              return (
                <React.Fragment key={vm.vm_id}>
                <tr
                    className={`transition-colors ${dirty ? "bg-amber-50" : "hover:bg-slate-50"}`}>
                  <td className="px-3 py-2 text-sm font-medium text-slate-800 whitespace-nowrap">
                    {vm.vm_name}
                  </td>
                  <td className="px-3 py-2 text-xs text-slate-500 font-mono whitespace-nowrap">
                    {vm.ip_address || <span className="text-slate-300">—</span>}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <HypervisorBadge type={vm.hypervisor_type} />
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <PowerBadge state={vm.power_state} />
                  </td>
                  <td className="px-3 py-2 text-sm text-slate-700 whitespace-nowrap">
                    <div className="flex items-center gap-1.5">
                      <span className="w-9 text-right tabular-nums">{vm.cpu_usage_pct}%</span>
                      <div className="w-14"><GaugeBar value={vm.cpu_usage_pct} /></div>
                    </div>
                  </td>
                  <td className="px-3 py-2 text-sm text-slate-600 whitespace-nowrap text-center">
                    {vm.cpu_cores}
                  </td>
                  <td className="px-3 py-2 text-sm text-slate-700 whitespace-nowrap tabular-nums">
                    {vm.ram_used_gb} GB
                  </td>
                  <td className="px-3 py-2 text-sm text-slate-500 whitespace-nowrap tabular-nums">
                    {vm.ram_total_gb} GB
                    <div className="w-14 mt-0.5"><GaugeBar value={vm.ram_usage_pct} /></div>
                  </td>

                  {/* ── Inline-editable Owner ─────────────────────────────── */}
                  <td className="px-2 py-1.5 whitespace-nowrap min-w-[130px]">
                    <input
                      value={getEdit(vm, "owner_name")}
                      onChange={e => setEdit(vm, "owner_name", e.target.value)}
                      onKeyDown={e => e.key === "Enter" && saveRow(vm)}
                      placeholder="Owner"
                      className={`w-full text-sm px-2 py-1 rounded border transition-colors
                        focus:outline-none focus:ring-2 focus:ring-blue-400
                        ${dirty
                          ? "border-amber-400 bg-amber-50"
                          : "border-transparent bg-transparent hover:border-slate-300 hover:bg-white"}`}
                    />
                  </td>

                  <td className="px-3 py-2 text-xs text-slate-500 whitespace-nowrap">
                    {vm.creation_date}
                  </td>

                  {/* ── Inline-editable Purpose ───────────────────────────── */}
                  <td className="px-2 py-1.5 min-w-[160px]">
                    <input
                      value={getEdit(vm, "purpose")}
                      onChange={e => setEdit(vm, "purpose", e.target.value)}
                      onKeyDown={e => e.key === "Enter" && saveRow(vm)}
                      placeholder="Purpose"
                      className={`w-full text-xs px-2 py-1 rounded border transition-colors
                        focus:outline-none focus:ring-2 focus:ring-blue-400
                        ${dirty
                          ? "border-amber-400 bg-amber-50"
                          : "border-transparent bg-transparent hover:border-slate-300 hover:bg-white"}`}
                    />
                  </td>

                  <td className="px-3 py-2 whitespace-nowrap">
                    <StatusDot status={vm.status} />
                  </td>

                  {/* ── Snapshot toggle ───────────────────────────────────── */}
                  <td className="px-2 py-1.5 whitespace-nowrap w-20">
                    <button
                      onClick={() => toggleSnap(vm.vm_id)}
                      title="View snapshots for this VM"
                      className={`text-[10px] px-2 py-1 rounded font-semibold transition-colors w-full
                        ${snapOpen[vm.vm_id]
                          ? "bg-indigo-100 text-indigo-700 border border-indigo-300"
                          : "bg-slate-100 text-slate-500 hover:bg-indigo-50 hover:text-indigo-600 border border-slate-200"}`}>
                      {snapOpen[vm.vm_id] ? "▲ Hide" : "▼ Snaps"}
                    </button>
                  </td>

                  {/* ── Save / spinner button (only when dirty) ───────────── */}
                  <td className="px-2 py-1.5 whitespace-nowrap w-16">
                    {dirty && (
                      saving[vm.vm_id]
                        ? <svg className="animate-spin h-4 w-4 text-blue-500 mx-auto"
                               viewBox="0 0 24 24" fill="none">
                            <circle className="opacity-25" cx="12" cy="12" r="10"
                                    stroke="currentColor" strokeWidth="4"/>
                            <path className="opacity-75" fill="currentColor"
                                  d="M4 12a8 8 0 018-8v8H4z"/>
                          </svg>
                        : <button
                            onClick={() => saveRow(vm)}
                            className="text-xs px-2 py-1 rounded bg-blue-600 text-white
                              hover:bg-blue-700 font-semibold transition-colors w-full">
                            Save
                          </button>
                    )}
                  </td>
                </tr>
                {/* ── Expandable snapshot panel row ─────────────────────── */}
                {snapOpen[vm.vm_id] && (
                  <tr className="bg-indigo-50/40">
                    <td colSpan={15} className="px-6 py-3">
                      <div className="flex items-center gap-2 mb-2">
                        <svg className="w-3.5 h-3.5 text-indigo-400" fill="none"
                             viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round"
                                d="M4 7v10c0 2 1 3 3 3h10c2 0 3-1 3-3V7M9 3h6M9 3a1 1 0 00-1 1v1h8V4a1 1 0 00-1-1H9z"/>
                        </svg>
                        <span className="text-xs font-semibold text-indigo-600">
                          Snapshots — {vm.vm_name}
                        </span>
                      </div>
                      <SnapshotPanel vmId={vm.vm_id} vmName={vm.vm_name} />
                    </td>
                  </tr>
                )}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="px-5 py-3 border-t border-slate-100 text-xs text-slate-400">
        Showing {displayed.length} of {vms.length} VMs
        {dirtyCount > 0 && (
          <span className="ml-3 text-amber-600">
            — {dirtyCount} row{dirtyCount > 1 ? "s" : ""} have unsaved edits
          </span>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// Main Dashboard
// ─────────────────────────────────────────────────────────────────────────────

// ─────────────────────────────────────────────────────────────────────────────
// CENTRALIZED FILTER STATE (Req 2)
// ─────────────────────────────────────────────────────────────────────────────
//
// All filtering is driven by a single `filters` object.
// Components read from it and write back via setFilters (functional updates).
//
// Shape:
//   {
//     hypervisorType: string | ""   ← Level 1 dropdown
//     serverId:       string | ""   ← Level 2 dropdown (cascades from L1)
//     powerState:     string | "all"
//     search:         string        ← smart global search
//   }
//
// Rules:
//   - Changing hypervisorType always resets serverId (cascade reset).
//   - The Level 2 server dropdown only shows servers of the selected hypervisor.
//   - All filters compose via logical AND through applyVMFilters().

const EMPTY_FILTERS = {
  hypervisorType: "",
  serverId:       "",
  powerState:     "all",
  search:         "",
};

export default function Dashboard({ onGoToServers, onGoToEmail }) {
  const { permissions } = useAuth();

  const [servers,     setServers]     = useState([]);
  const [vms,         setVms]         = useState([]);
  const [hypervisors, setHypervisors] = useState([]);
  const [loading,     setLoading]     = useState(true);
  const [fetching,    setFetching]    = useState(false);
  const [refreshing,  setRefreshing]  = useState(false);
  const [sending,     setSending]     = useState(false);
  const [sendMsg,     setSendMsg]     = useState("");
  const [sendFmt,     setSendFmt]     = useState("both"); // "html" | "csv" | "both"
  const [error,       setError]       = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);

  // ── Centralized reactive filter state (Req 2) ─────────────────────────────
  const [filters,    setFilters]     = useState(EMPTY_FILTERS);

  // Selected server IDs for export
  const [selectedIds, setSelectedIds] = useState(new Set());
  const intervalRef = useRef(null);

  // ── Level 2 server list — only servers matching the selected hypervisor ────
  const l2Servers = useMemo(
    () => filters.hypervisorType
      ? servers.filter(s => s.hypervisor_type === filters.hypervisorType)
      : servers,
    [servers, filters.hypervisorType]
  );

  // ── Cascade reset: changing L1 clears L2 and powerState ──────────────────
  const setHypervisorFilter = (hvType) => {
    setFilters(f => ({
      ...f,
      hypervisorType: hvType,
      serverId:       "",    // always reset Level 2 when Level 1 changes
    }));
  };

  const fetchAll = useCallback(async (isBackground = false) => {
    if (isBackground) setFetching(true);
    try {
      const [sRes, vRes, hRes] = await Promise.all([
        axios.get(`${API}/api/servers`),
        axios.get(`${API}/api/vms`),
        axios.get(`${API}/api/hypervisors`),
      ]);
      setServers(sRes.data);
      setVms(vRes.data);
      setHypervisors(hRes.data);
      setLastUpdated(new Date().toLocaleTimeString());
      setError(null);
    } catch (err) {
      const status = err?.response?.status;
      if (status === 403) {
        setError("Permission denied — your account needs the 'dashboard_view' permission. Ask an Administrator to assign you to a group (e.g. Team, Leads, or Administrator).");
      } else if (status === 401) {
        // axios interceptor in AuthContext will clear token and redirect to login
        setError("Session expired. Please log in again.");
      } else {
        setError("Unable to reach the API. Retrying…");
      }
    } finally {
      setLoading(false);
      setFetching(false);
    }
  }, []);

  useEffect(() => {
    fetchAll(false);
    intervalRef.current = setInterval(() => fetchAll(true), POLL_MS);
    return () => clearInterval(intervalRef.current);
  }, [fetchAll]);

  // ── Permission guard — show a clear message before any API calls ──────────
  if (permissions && permissions.dashboard_view === false) {
    return (
      <div className="rounded-xl bg-amber-50 border border-amber-200 p-8 text-center text-amber-800 text-sm max-w-lg mx-auto mt-16">
        <svg className="w-10 h-10 mx-auto mb-3 text-amber-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round"
                d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
        </svg>
        <p className="font-bold text-base mb-1">Dashboard access not granted</p>
        <p className="text-amber-700">Your account does not have the <strong>dashboard_view</strong> permission.<br/>
        Ask an Administrator to add you to a group with dashboard access (e.g. <em>Team</em> or <em>Leads</em>).</p>
      </div>
    );
  }

  // ── Manual cache refresh ────────────────────────────────────────────────────
  const handleRefreshNow = async () => {
    setRefreshing(true);
    try {
      await axios.post(`${API}/api/cache/refresh`, {});
      await fetchAll(false);
    } catch { /* fetchAll shows error */ }
    finally { setRefreshing(false); }
  };

  // ── Server selection helpers ────────────────────────────────────────────────
  const toggleSelect = (serverId, checked) => {
    setSelectedIds(s => {
      const n = new Set(s);
      checked ? n.add(serverId) : n.delete(serverId);
      return n;
    });
  };
  const selectAll    = () => setSelectedIds(new Set(servers.map(s => s.server_id)));
  const selectNone   = () => setSelectedIds(new Set());
  const allSelected  = servers.length > 0 && selectedIds.size === servers.length;
  const noneSelected = selectedIds.size === 0;

  // ── Export selected servers to presentation CSV ─────────────────────────────
  const handleExport = () => {
    const selServers = servers.filter(s =>
      selectedIds.size === 0 ? true : selectedIds.has(s.server_id)
    );
    const selVms = vms.filter(v =>
      selectedIds.size === 0 ? true : selectedIds.has(v.host_server_id)
    );
    const csv = buildPresentationCsv(selServers, selVms);
    const ts  = new Date().toISOString().slice(0, 16).replace("T", "_").replace(":", "");
    const label = selectedIds.size > 0 ? `${selectedIds.size}servers` : "all";
    triggerCsvDownload(csv, `hypermonitor_report_${label}_${ts}.csv`);
  };

  // ── Derived KPIs ─────────────────────────────────────────────────────────────
  const totalVMs      = vms.length;
  const runningVMs    = vms.filter(v => v.power_state === "running").length;
  const criticalCount = servers.filter(h => h.status === "critical").length;
  const warningCount  = servers.filter(h => h.status === "warning").length;
  const avgCpu        = servers.length
    ? (servers.reduce((s, h) => s + h.cpu_usage_pct, 0) / servers.length).toFixed(1)
    : 0;

  // ── Loading ────────────────────────────────────────────────────────────────
  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-32 gap-4">
        <svg className="animate-spin h-10 w-10 text-blue-500" viewBox="0 0 24 24" fill="none">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
        </svg>
        <p className="text-slate-500 text-sm">Querying hypervisors…</p>
      </div>
    );
  }

  // ── Error state (no servers loaded yet) ───────────────────────────────────
  if (error && servers.length === 0) {
    const isPermError = error.startsWith("Permission denied");
    return (
      <div className={`rounded-xl border p-6 text-sm ${
        isPermError
          ? "bg-amber-50 border-amber-200 text-amber-800"
          : "bg-red-50 border-red-200 text-red-700"
      }`}>
        <strong>{isPermError ? "Access denied:" : "Connection error:"}</strong> {error}
        {isPermError && (
          <p className="mt-3 text-xs text-amber-600">
            Sign in as <strong>root</strong> → go to <strong>Users &amp; Groups</strong> → edit this user → assign the <strong>Team</strong>, <strong>Leads</strong>, or <strong>Administrator</strong> group.
          </p>
        )}
      </div>
    );
  }

  // ── Empty state ────────────────────────────────────────────────────────────
  if (!loading && servers.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-5 text-center px-6">
        <svg className="w-20 h-20 text-slate-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.1}
                d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2
                   M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2" />
        </svg>
        <div>
          <p className="text-slate-700 font-bold text-xl">No servers to monitor yet</p>
          <p className="text-slate-400 text-sm mt-1 max-w-sm">
            Go to <strong>Manage Servers</strong> to add your first hypervisor host.
          </p>
        </div>
        {onGoToServers && (
          <button onClick={onGoToServers}
                  className="mt-2 bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold
                    px-6 py-3 rounded-xl flex items-center gap-2">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4"/>
            </svg>
            Go to Manage Servers
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-8">

      {/* ── Page header ─────────────────────────────────────────────────────── */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <div>
          <h1 className="text-2xl font-bold text-slate-800">Server Overview</h1>
          <p className="text-sm text-slate-400 flex items-center gap-2">
            <span>Live data · refreshes every {POLL_MS / 1000} s</span>
            {lastUpdated && <span>· Updated {lastUpdated}</span>}
            {fetching && (
              <svg className="animate-spin h-3 w-3 text-blue-400" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
              </svg>
            )}
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {error && (
            <span className="text-xs text-amber-600 bg-amber-50 border border-amber-200 px-3 py-1 rounded-full">
              ⚠ Fetch error — showing last known data
            </span>
          )}

          {/* Export Selected CSV */}
          <button
            onClick={handleExport}
            disabled={servers.length === 0}
            title={selectedIds.size > 0
              ? `Export ${selectedIds.size} selected server(s) to CSV`
              : "Export all servers to CSV"}
            className="flex items-center gap-1.5 text-xs font-semibold
              border border-indigo-400 text-indigo-700 px-3 py-1.5 rounded-lg
              hover:bg-indigo-50 disabled:opacity-40 disabled:cursor-not-allowed">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round"
                    d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
            </svg>
            {selectedIds.size > 0 ? `Export ${selectedIds.size} Selected` : "Export All CSV"}
          </button>

          {/* Refresh Now */}
          <button
            disabled={refreshing || servers.length === 0}
            onClick={handleRefreshNow}
            className="flex items-center gap-1.5 text-xs font-semibold
              border border-emerald-400 text-emerald-700 px-3 py-1.5 rounded-lg
              hover:bg-emerald-50 disabled:opacity-40 disabled:cursor-not-allowed">
            {refreshing
              ? <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
                </svg>
              : <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                  <path strokeLinecap="round" strokeLinejoin="round"
                        d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                </svg>}
            {refreshing ? "Refreshing…" : "Refresh Now"}
          </button>

          {/* Send Report — split button with format selector */}
          <div className="flex items-center rounded-lg border border-slate-300 overflow-hidden divide-x divide-slate-300">
            {/* Main action button */}
            <button
              disabled={sending || servers.length === 0}
              onClick={async () => {
                setSending(true); setSendMsg("");
                try {
                  const res = await axios.post(`${API}/api/email/send-report`,
                    { report_format: sendFmt });
                  setSendMsg(`✓ ${res.data.message}`);
                } catch (e) {
                  const detail = e.response?.data?.detail || "Send failed. Check Email Reports settings.";
                  setSendMsg(`⚠ ${detail}`);
                } finally {
                  setSending(false);
                  setTimeout(() => setSendMsg(""), 7000);
                }
              }}
              className="flex items-center gap-1.5 text-xs font-semibold text-slate-600 px-3 py-1.5
                hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed bg-white">
              {sending
                ? <svg className="animate-spin h-3.5 w-3.5" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
                  </svg>
                : <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round"
                          d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
                  </svg>}
              {sending ? "Sending…" : "Send Report"}
            </button>
            {/* Format picker */}
            <select
              disabled={sending || servers.length === 0}
              value={sendFmt}
              onChange={e => setSendFmt(e.target.value)}
              title="Select report format"
              className="text-xs text-slate-500 bg-white px-2 py-1.5 appearance-none
                focus:outline-none hover:bg-slate-50 disabled:opacity-40 cursor-pointer">
              <option value="both">HTML+CSV</option>
              <option value="html">HTML only</option>
              <option value="csv">CSV only</option>
            </select>
          </div>

          {onGoToEmail && (
            <button onClick={onGoToEmail}
                    className="flex items-center gap-1.5 text-xs font-semibold text-slate-500
                      border border-slate-200 px-3 py-1.5 rounded-lg hover:bg-slate-50">
              ⚙ Email Settings
            </button>
          )}
          {onGoToServers && (
            <button onClick={onGoToServers}
                    className="flex items-center gap-1.5 text-xs font-semibold text-blue-600
                      border border-blue-300 px-3 py-1.5 rounded-lg hover:bg-blue-50">
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4"/>
              </svg>
              Add Server
            </button>
          )}
        </div>
      </div>

      {/* Send report toast */}
      {sendMsg && (
        <div className={`text-sm px-4 py-2.5 rounded-xl border ${
          sendMsg.startsWith("✓")
            ? "bg-emerald-50 border-emerald-200 text-emerald-800"
            : "bg-amber-50 border-amber-200 text-amber-800"
        }`}>
          {sendMsg}
        </div>
      )}

      {/* ── Critical alert banner ─────────────────────────────────────────── */}
      {criticalCount > 0 && (
        <div className="flex items-center gap-3 bg-red-50 border border-red-300 rounded-xl px-5 py-3 text-sm text-red-800">
          <svg className="w-5 h-5 shrink-0 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round"
                  d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
          </svg>
          <div className="flex-1">
            <span className="font-bold">Critical alert</span>
            {" — "}
            {servers.filter(s => s.status === "critical").map(s => s.display_name).join(", ")}
            {" "}{criticalCount === 1 ? "is" : "are"} at critical CPU utilisation (&gt;90%).
            {servers.some(s => s.status === "critical" && s.error)
              && " Connection errors detected — check credentials."}
          </div>
        </div>
      )}

      {/* ── KPI strip ────────────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <StatCard label="Total Hosts"  value={servers.length} sub="across all hypervisors" />
        <StatCard label="Total VMs"    value={totalVMs}
                  sub={`${runningVMs} running · ${totalVMs - runningVMs} stopped`} />
        <StatCard label="Avg CPU"      value={`${avgCpu}%`}
                  sub="live across all hosts" accent="text-blue-600" />
        <StatCard label="Alerts"
                  value={criticalCount + warningCount}
                  sub={`${criticalCount} critical · ${warningCount} warning`}
                  accent={criticalCount > 0 ? "text-red-600" : "text-amber-500"} />
      </div>

      {/* ── Req 2 — Level 1 + Level 2 Composable Filter Dropdowns ─────────── */}
      <section className="bg-white rounded-xl border border-slate-200 shadow-sm px-5 py-4">
        <div className="flex flex-wrap items-end gap-4">
          {/* Level 1 — Hypervisor Type */}
          <div className="flex flex-col gap-1 min-w-[180px]">
            <label className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              Level 1 — Hypervisor
            </label>
            <select
              value={filters.hypervisorType}
              onChange={e => setHypervisorFilter(e.target.value)}
              className="border border-slate-300 rounded-lg px-3 py-2 text-sm
                focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white">
              <option value="">All Hypervisors</option>
              {hypervisors.map(hv => (
                <option key={hv.name} value={hv.name}>
                  {hv.name} ({hv.server_count} hosts · {hv.vm_count} VMs)
                </option>
              ))}
            </select>
          </div>

          {/* Level 2 — Server (cascades from L1) */}
          <div className="flex flex-col gap-1 min-w-[200px]">
            <label className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              Level 2 — Server Host
            </label>
            <select
              value={filters.serverId}
              onChange={e => setFilters(f => ({ ...f, serverId: e.target.value }))}
              disabled={l2Servers.length === 0}
              className="border border-slate-300 rounded-lg px-3 py-2 text-sm
                focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white
                disabled:opacity-50 disabled:cursor-not-allowed">
              <option value="">
                {l2Servers.length === 0
                  ? "No servers available"
                  : `All Servers (${l2Servers.length})`}
              </option>
              {l2Servers.map(s => (
                <option key={s.server_id} value={s.server_id}>
                  {s.display_name} ({s.ip_address})
                </option>
              ))}
            </select>
          </div>

          {/* Active filter pills + reset */}
          <div className="flex flex-wrap gap-2 items-center ml-auto">
            {filters.hypervisorType && (
              <span className="inline-flex items-center gap-1 text-xs bg-blue-100 text-blue-700
                border border-blue-200 px-2 py-0.5 rounded-full font-medium">
                <HypervisorBadge type={filters.hypervisorType} />
                <button onClick={() => setHypervisorFilter("")}
                        className="ml-1 hover:text-blue-900">✕</button>
              </span>
            )}
            {filters.serverId && (
              <span className="inline-flex items-center gap-1 text-xs bg-slate-100
                text-slate-700 border border-slate-200 px-2 py-1 rounded-full font-medium">
                {l2Servers.find(s => s.server_id === filters.serverId)?.display_name
                  || filters.serverId}
                <button onClick={() => setFilters(f => ({ ...f, serverId: "" }))}
                        className="ml-1 hover:text-slate-900">✕</button>
              </span>
            )}
            {(filters.hypervisorType || filters.serverId ||
              (filters.powerState && filters.powerState !== "all") ||
              filters.search) && (
              <button onClick={() => setFilters(EMPTY_FILTERS)}
                      className="text-xs text-red-500 hover:text-red-700 underline font-semibold">
                Reset all filters
              </button>
            )}
          </div>
        </div>
      </section>

      {/* ── Hypervisor summary — clickable filter cards ───────────────────── */}
      {hypervisors.length > 0 && (
        <section>
          <h2 className="text-base font-semibold text-slate-700 mb-3">Hypervisor Summary</h2>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            {hypervisors.map(hv => (
              <button key={hv.name}
                      onClick={() => setHypervisorFilter(
                        filters.hypervisorType === hv.name ? "" : hv.name
                      )}
                      className={`text-left rounded-xl border p-4 transition-all ${
                        filters.hypervisorType === hv.name
                          ? "border-blue-400 bg-blue-50 shadow-sm"
                          : "border-slate-200 bg-white hover:border-blue-300"
                      }`}>
                <div className="flex items-center justify-between mb-2">
                  <HypervisorBadge type={hv.name} />
                  <span className="text-xs text-slate-400">{hv.server_count} hosts</span>
                </div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                  <span className="text-slate-500">VMs</span>
                  <span className="font-semibold text-slate-700">{hv.vm_count}</span>
                  <span className="text-slate-500">Avg CPU</span>
                  <span className="font-semibold text-slate-700">{hv.avg_cpu_pct}%</span>
                  <span className="text-slate-500">Avg RAM</span>
                  <span className="font-semibold text-slate-700">{hv.avg_ram_pct}%</span>
                </div>
              </button>
            ))}
          </div>
        </section>
      )}

      {/* ── Server cards — selectable for export ─────────────────────────── */}
      <section>
        <div className="flex items-center gap-3 mb-3">
          <h2 className="text-base font-semibold text-slate-700">Live Host Utilisation</h2>
          <div className="flex gap-2 text-xs ml-auto">
            <button onClick={allSelected ? selectNone : selectAll}
                    className="text-slate-400 hover:text-slate-700 underline">
              {allSelected ? "Deselect all" : "Select all"}
            </button>
            {!noneSelected && !allSelected && (
              <button onClick={selectNone}
                      className="text-slate-400 hover:text-slate-700 underline">
                Clear
              </button>
            )}
            {selectedIds.size > 0 && (
              <span className="text-indigo-600 font-semibold">
                {selectedIds.size} selected
              </span>
            )}
          </div>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
          {servers
            .filter(s =>
              (!filters.hypervisorType || s.hypervisor_type === filters.hypervisorType) &&
              (!filters.serverId       || s.server_id       === filters.serverId)
            )
            .map(s => (
              <ServerCard
                key={s.server_id}
                server={s}
                selected={selectedIds.has(s.server_id)}
                onSelect={toggleSelect}
              />
            ))}
        </div>

        {selectedIds.size > 0 && (
          <p className="mt-3 text-xs text-indigo-600 flex items-center gap-1">
            <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round"
                    d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4"/>
            </svg>
            {selectedIds.size} server{selectedIds.size > 1 ? "s" : ""} selected —
            click <strong>Export {selectedIds.size} Selected</strong> in the header to download a presentation-ready CSV.
          </p>
        )}
      </section>

      {/* ── VM inventory — centralized filter state drives everything ──────── */}
      <section>
        <VMTable
          vms={vms}
          filters={filters}
          onFilters={setFilters}
          onVmsUpdated={() => fetchAll(true)}
        />
      </section>

    </div>
  );
}
