/**
 * ManageServers.jsx — "Manage Servers" page.
 *
 * Tabs: Servers | VM Metadata
 *
 * Servers tab flow after "Add Server":
 *   1. POST /api/servers/config         → saves identity + encrypted credentials
 *   2. POST /api/servers/probe/{id}     → SSH into the host, read RAM/CPU/disk,
 *      write values back to DB automatically (no manual entry needed)
 *   3. Card updates to show detected specs (or a friendly error if SSH fails)
 *
 * VM Metadata tab:
 *   • Lists all vm_metadata rows from PostgreSQL
 *   • Inline edit: owner_name, creation_date, purpose
 *   • PUT /api/vms/metadata/{vm_id} — creates or updates the record
 */

import React, { useCallback, useEffect, useState } from "react";
import axios from "axios";

const API = process.env.REACT_APP_API_URL || "";

// Must stay in sync with hypervisors/__init__.py REGISTRY keys.
// To add Proxmox: uncomment "Proxmox VE" here AND in the backend registry.
const HV_TYPES = ["VMware ESXi", "Ubuntu KVM", "Hyper-V" /*, "Proxmox VE" */];

const HV_STYLE = {
  "VMware ESXi": { pill: "bg-blue-100 text-blue-800",   ring: "ring-blue-300",   icon: "🖥" },
  "Ubuntu KVM":  { pill: "bg-orange-100 text-orange-800", ring: "ring-orange-300", icon: "🐧" },
  "Hyper-V":     { pill: "bg-purple-100 text-purple-800", ring: "ring-purple-300", icon: "🪟" },
};

// Probe status → badge style
const PROBE_BADGE = {
  pending:     { bg: "bg-slate-100 text-slate-500",    label: "Pending detection" },
  ok:          { bg: "bg-emerald-50 text-emerald-700", label: "Specs detected" },
  failed:      { bg: "bg-red-50 text-red-600",         label: "Detection failed" },
  unsupported: { bg: "bg-amber-50 text-amber-700",     label: "Manual spec needed" },
};

const EMPTY_FORM = {
  display_name:    "",
  ip_address:      "",
  hostname:        "",
  hypervisor_type: "VMware ESXi",
  username:        "",
  password:        "",
  enabled:         true,
};

// ── Tiny shared primitives ────────────────────────────────────────────────────

function Label({ children }) {
  return (
    <label className="block text-xs font-semibold text-slate-600 mb-1">
      {children}
    </label>
  );
}

function Input({ className = "", ...props }) {
  return (
    <input
      className={`w-full border border-slate-300 rounded-lg px-3 py-2 text-sm
        focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white ${className}`}
      {...props}
    />
  );
}

function Select({ children, ...props }) {
  return (
    <select
      className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm
        focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white"
      {...props}
    >
      {children}
    </select>
  );
}

// ── Add / Edit Server Modal ───────────────────────────────────────────────────

function ServerModal({ editing, onClose, onSaved }) {
  const [form, setForm]       = useState(editing
    ? { ...editing, username: "", password: "" }
    : { ...EMPTY_FORM });
  const [saving, setSaving]   = useState(false);
  const [error, setError]     = useState("");
  const [showPwd, setShowPwd] = useState(false);

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!form.display_name.trim()) { setError("Display name is required."); return; }
    if (!form.ip_address.trim())   { setError("IP address is required."); return; }
    setSaving(true);
    setError("");
    try {
      let saved;
      if (editing) {
        const res = await axios.put(`${API}/api/servers/config/${editing.server_id}`, form);
        saved = res.data;
      } else {
        const res = await axios.post(`${API}/api/servers/config`, form);
        saved = res.data;
      }
      // Trigger hardware detection automatically — fire-and-forget (no await)
      // The ManageServers list will refresh and pick up the probe result.
      axios.post(`${API}/api/servers/probe/${saved.server_id}`).catch(() => {});
      onSaved();
      onClose();
    } catch (err) {
      const detail = err.response?.data?.detail;
      setError(Array.isArray(detail)
        ? detail.map(d => d.msg).join("; ")
        : (detail || "Save failed."));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         style={{ background: "rgba(15,23,42,0.55)" }}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg max-h-[90vh] overflow-y-auto">

        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100">
          <h2 className="font-bold text-slate-800 text-lg">
            {editing ? "Edit Server" : "Add New Server"}
          </h2>
          <button onClick={onClose}
                  className="text-slate-400 hover:text-slate-700 text-2xl leading-none font-light">
            ×
          </button>
        </div>

        <form onSubmit={handleSubmit} className="px-6 py-5 flex flex-col gap-4">

          {/* ── Server Identity ────────────────────────────────────── */}
          <p className="text-xs font-bold uppercase tracking-wider text-slate-400">
            Server Identity
          </p>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <Label>Display Name <span className="text-red-500">*</span></Label>
              <Input placeholder="e.g. Prod KVM Host 01"
                     value={form.display_name}
                     onChange={e => set("display_name", e.target.value)} />
            </div>
            <div>
              <Label>Hypervisor Type <span className="text-red-500">*</span></Label>
              <Select value={form.hypervisor_type}
                      onChange={e => set("hypervisor_type", e.target.value)}>
                {HV_TYPES.map(t => <option key={t}>{t}</option>)}
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <Label>IP Address <span className="text-red-500">*</span></Label>
              <Input placeholder="e.g. 192.168.1.100"
                     value={form.ip_address}
                     onChange={e => set("ip_address", e.target.value)} />
            </div>
            <div>
              <Label>Hostname (optional)</Label>
              <Input placeholder="e.g. kvm01.corp.local"
                     value={form.hostname}
                     onChange={e => set("hostname", e.target.value)} />
            </div>
          </div>

          {/* ── Credentials ────────────────────────────────────────── */}
          <p className="text-xs font-bold uppercase tracking-wider text-slate-400 mt-1">
            Credentials
            {editing && (
              <span className="ml-2 font-normal normal-case text-slate-400">
                — leave blank to keep existing
              </span>
            )}
          </p>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <Label>Username</Label>
              <Input placeholder="root / administrator"
                     autoComplete="new-password"
                     value={form.username}
                     onChange={e => set("username", e.target.value)} />
            </div>
            <div>
              <Label>Password</Label>
              <div className="relative">
                <Input type={showPwd ? "text" : "password"}
                       placeholder="••••••••"
                       autoComplete="new-password"
                       value={form.password}
                       onChange={e => set("password", e.target.value)}
                       className="pr-12" />
                <button type="button" tabIndex={-1}
                        onClick={() => setShowPwd(p => !p)}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 text-xs select-none">
                  {showPwd ? "Hide" : "Show"}
                </button>
              </div>
            </div>
          </div>

          {/* ── Auto-detect notice ─────────────────────────────────── */}
          <div className="flex items-start gap-3 bg-blue-50 border border-blue-200 rounded-xl px-4 py-3 text-xs text-blue-800">
            <svg className="w-4 h-4 mt-0.5 shrink-0 text-blue-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                    d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/>
            </svg>
            <span>
              <strong>RAM, CPU and Storage are detected automatically</strong> via SSH / API after
              saving. No manual entry needed. You can re-run detection anytime using the
              "Detect Specs" button on the server card.
            </span>
          </div>

          {/* ── Enabled toggle ──────────────────────────────────────── */}
          <label className="flex items-center gap-3 cursor-pointer select-none">
            <div className="relative">
              <input type="checkbox" className="sr-only"
                     checked={form.enabled}
                     onChange={e => set("enabled", e.target.checked)} />
              <div className={`w-10 h-5 rounded-full transition-colors ${form.enabled ? "bg-emerald-500" : "bg-slate-300"}`} />
              <div className={`absolute top-0.5 left-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${form.enabled ? "translate-x-5" : ""}`} />
            </div>
            <span className="text-sm text-slate-600">
              {form.enabled ? "Enabled — will appear in monitoring" : "Disabled — hidden from dashboard"}
            </span>
          </label>

          {/* Error */}
          {error && (
            <div className="text-sm text-red-600 bg-red-50 border border-red-200 rounded-lg px-4 py-2">
              {error}
            </div>
          )}

          {/* Footer buttons */}
          <div className="flex gap-3 justify-end pt-1">
            <button type="button" onClick={onClose}
                    className="px-4 py-2 rounded-lg text-sm text-slate-600 border border-slate-300 hover:bg-slate-50">
              Cancel
            </button>
            <button type="submit" disabled={saving}
                    className="px-5 py-2 rounded-lg text-sm font-semibold bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 flex items-center gap-2">
              {saving && (
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
                </svg>
              )}
              {editing ? "Save Changes" : "Add Server"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Delete Confirmation ───────────────────────────────────────────────────────

function ConfirmDelete({ server, onConfirm, onCancel }) {
  const [busy, setBusy] = useState(false);
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
         style={{ background: "rgba(15,23,42,0.55)" }}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-6 flex flex-col gap-4">
        <h2 className="font-bold text-slate-800 text-lg">Remove Server?</h2>
        <p className="text-sm text-slate-600">
          <span className="font-semibold">{server.display_name}</span> ({server.ip_address}) will be
          permanently removed. This cannot be undone.
        </p>
        <div className="flex gap-3 justify-end">
          <button onClick={onCancel}
                  className="px-4 py-2 rounded-lg text-sm border border-slate-300 hover:bg-slate-50">
            Cancel
          </button>
          <button disabled={busy}
                  onClick={async () => { setBusy(true); await onConfirm(); setBusy(false); }}
                  className="px-4 py-2 rounded-lg text-sm font-semibold bg-red-600 text-white hover:bg-red-700 disabled:opacity-50">
            {busy ? "Removing…" : "Yes, Remove"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Spec chip — shows one detected value ─────────────────────────────────────

function SpecChip({ icon, label, value, detected }) {
  return (
    <div className={`flex flex-col items-center px-3 py-2 rounded-lg border text-center min-w-[72px]
      ${detected ? "bg-white border-slate-200" : "bg-slate-50 border-dashed border-slate-200"}`}>
      <span className="text-slate-400 text-xs mb-0.5">{icon}</span>
      <span className={`font-bold text-sm ${detected ? "text-slate-800" : "text-slate-400"}`}>
        {detected ? value : "—"}
      </span>
      <span className="text-slate-400 text-[10px]">{label}</span>
    </div>
  );
}

// ── Server Card ───────────────────────────────────────────────────────────────

function ServerRow({ server, onEdit, onDelete, onToggle, onProbe, probing }) {
  const hv      = HV_STYLE[server.hypervisor_type] || HV_STYLE["Ubuntu KVM"];
  const probe   = PROBE_BADGE[server.probe_status] || PROBE_BADGE.pending;
  const detected = server.probe_status === "ok";

  return (
    <div className={`bg-white border border-slate-200 rounded-xl overflow-hidden transition-opacity ${server.enabled ? "" : "opacity-60"}`}>

      {/* Top bar */}
      <div className="flex flex-col sm:flex-row sm:items-center gap-3 px-5 py-4">

        {/* Identity */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold text-slate-800 text-sm">{server.display_name}</span>
            <span className={`text-xs px-2 py-0.5 rounded-full font-semibold ${hv.pill}`}>
              {hv.icon} {server.hypervisor_type}
            </span>
            {server.has_credentials && (
              <span className="text-xs bg-emerald-50 text-emerald-700 border border-emerald-200 px-2 py-0.5 rounded-full">
                🔒 Creds stored
              </span>
            )}
            {!server.enabled && (
              <span className="text-xs bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full">
                Disabled
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-slate-500 font-mono">
            {server.ip_address}
            {server.hostname ? ` · ${server.hostname}` : ""}
          </p>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-2 shrink-0 flex-wrap">
          <button onClick={() => onToggle(server.server_id)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-semibold border transition-colors ${
                    server.enabled
                      ? "border-slate-300 text-slate-600 hover:bg-slate-50"
                      : "border-emerald-400 text-emerald-700 hover:bg-emerald-50"
                  }`}>
            {server.enabled ? "Disable" : "Enable"}
          </button>
          <button onClick={() => onEdit(server)}
                  className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-blue-300 text-blue-700 hover:bg-blue-50">
            Edit
          </button>
          <button onClick={() => onDelete(server)}
                  className="px-3 py-1.5 rounded-lg text-xs font-semibold border border-red-300 text-red-600 hover:bg-red-50">
            Remove
          </button>
        </div>
      </div>

      {/* Detected specs bar */}
      <div className="px-5 pb-4 flex flex-col sm:flex-row sm:items-center gap-3">

        {/* Spec chips */}
        <div className="flex gap-2 flex-wrap">
          <SpecChip icon="🧠" label="RAM"
                    value={`${server.ram_total_gb} GB`}
                    detected={detected && server.ram_total_gb > 0} />
          <SpecChip icon="⚙️" label="CPUs"
                    value={`${server.cpu_cores}`}
                    detected={detected && server.cpu_cores > 0} />
          <SpecChip icon="💾" label="Disk"
                    value={`${server.storage_total_tb} TB`}
                    detected={detected && server.storage_total_tb > 0} />
        </div>

        {/* Probe status + button */}
        <div className="flex items-center gap-2 sm:ml-auto flex-wrap">
          <span className={`text-xs px-2 py-1 rounded-full font-medium ${probe.bg}`}>
            {probe.label}
          </span>

          {/* Re-detect / Detect Specs button */}
          {server.has_credentials ? (
            <button onClick={() => onProbe(server.server_id)}
                    disabled={probing}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold
                      bg-slate-800 text-white hover:bg-slate-700 disabled:opacity-50 transition-colors">
              {probing ? (
                <>
                  <svg className="animate-spin h-3 w-3" viewBox="0 0 24 24" fill="none">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
                  </svg>
                  Detecting…
                </>
              ) : (
                <>
                  <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                  </svg>
                  {detected ? "Re-detect Specs" : "Detect Specs"}
                </>
              )}
            </button>
          ) : (
            <span className="text-xs text-slate-400 italic">Add credentials to auto-detect</span>
          )}
        </div>
      </div>
    </div>
  );
}

// ── VM Metadata inline editor ─────────────────────────────────────────────────

function VMMetadataEditor({ record, onSaved }) {
  const [editing, setEditing]   = useState(false);
  const [form, setForm]         = useState({
    owner_name:    record.owner_name    || "",
    creation_date: record.creation_date || "",
    purpose:       record.purpose       || "",
  });
  const [saving, setSaving]     = useState(false);
  const [err, setErr]           = useState("");

  const reset = () => {
    setForm({
      owner_name:    record.owner_name    || "",
      creation_date: record.creation_date || "",
      purpose:       record.purpose       || "",
    });
    setErr("");
    setEditing(false);
  };

  const save = async () => {
    setSaving(true);
    setErr("");
    try {
      await axios.put(`${API}/api/vms/metadata/${record.vm_id}`, {
        vm_id:          record.vm_id,
        vm_name:        record.vm_name,
        ip_address:     record.ip_address,
        hypervisor_type: record.hypervisor_type,
        ...form,
      });
      onSaved();
      setEditing(false);
    } catch (e) {
      setErr(e.response?.data?.detail || "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  if (!editing) {
    return (
      <tr className="hover:bg-slate-50 transition-colors">
        <td className="px-3 py-3 text-sm font-medium text-slate-800 whitespace-nowrap">{record.vm_name}</td>
        <td className="px-3 py-3 text-xs text-slate-500 font-mono whitespace-nowrap">{record.ip_address || "—"}</td>
        <td className="px-3 py-3 whitespace-nowrap">
          <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold
            ${{ "VMware ESXi": "bg-blue-100 text-blue-800", "Ubuntu KVM": "bg-orange-100 text-orange-800", "Hyper-V": "bg-purple-100 text-purple-800" }[record.hypervisor_type] || "bg-slate-100 text-slate-600"}`}>
            {record.hypervisor_type || "—"}
          </span>
        </td>
        <td className="px-3 py-3 text-sm text-slate-600 whitespace-nowrap">{record.owner_name || <span className="text-slate-300 italic">unset</span>}</td>
        <td className="px-3 py-3 text-xs text-slate-500 whitespace-nowrap">{record.creation_date || "—"}</td>
        <td className="px-3 py-3 text-xs text-slate-500 max-w-[180px] truncate" title={record.purpose}>
          {record.purpose || <span className="text-slate-300 italic">unset</span>}
        </td>
        <td className="px-3 py-3 whitespace-nowrap">
          <button onClick={() => setEditing(true)}
                  className="text-xs px-2 py-1 rounded border border-blue-300 text-blue-700 hover:bg-blue-50 font-semibold">
            Edit
          </button>
        </td>
      </tr>
    );
  }

  return (
    <tr className="bg-blue-50">
      <td className="px-3 py-2 text-sm font-medium text-slate-800 whitespace-nowrap" colSpan={3}>
        <span className="font-semibold">{record.vm_name}</span>
        <span className="ml-2 text-xs text-slate-400 font-mono">{record.ip_address}</span>
      </td>
      <td className="px-3 py-2">
        <input className="w-full border border-slate-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
               value={form.owner_name}
               onChange={e => setForm(f => ({ ...f, owner_name: e.target.value }))}
               placeholder="Owner name" />
      </td>
      <td className="px-3 py-2">
        <input type="date"
               className="w-full border border-slate-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
               value={form.creation_date}
               onChange={e => setForm(f => ({ ...f, creation_date: e.target.value }))} />
      </td>
      <td className="px-3 py-2">
        <input className="w-full border border-slate-300 rounded px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-400"
               value={form.purpose}
               onChange={e => setForm(f => ({ ...f, purpose: e.target.value }))}
               placeholder="e.g. Web Server" />
        {err && <p className="text-red-500 text-[10px] mt-0.5">{err}</p>}
      </td>
      <td className="px-3 py-2 whitespace-nowrap">
        <div className="flex gap-1">
          <button onClick={save} disabled={saving}
                  className="text-xs px-2 py-1 rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 font-semibold">
            {saving ? "…" : "Save"}
          </button>
          <button onClick={reset}
                  className="text-xs px-2 py-1 rounded border border-slate-300 text-slate-500 hover:bg-slate-100">
            Cancel
          </button>
        </div>
      </td>
    </tr>
  );
}

// ── VM Metadata Tab ───────────────────────────────────────────────────────────

function VMMetadataTab() {
  const [records, setRecords] = useState([]);
  const [loading, setLoading] = useState(true);
  const [search,  setSearch]  = useState("");

  const load = useCallback(async () => {
    try {
      const res = await axios.get(`${API}/api/vms/metadata`);
      setRecords(res.data);
    } catch { /* silently ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const displayed = records.filter(r => {
    const q = search.toLowerCase();
    return !q ||
      r.vm_name.toLowerCase().includes(q)        ||
      (r.owner_name || "").toLowerCase().includes(q) ||
      (r.purpose || "").toLowerCase().includes(q);
  });

  if (loading) {
    return <p className="text-sm text-slate-400 py-8 text-center">Loading metadata…</p>;
  }

  if (records.length === 0) {
    return (
      <div className="py-16 text-center">
        <p className="text-slate-500 font-medium">No VM metadata records yet.</p>
        <p className="text-sm text-slate-400 mt-1">
          Records appear here once VMs are discovered from your hypervisors via the Dashboard.
          You can also add them manually using the API (<code>/api/vms/metadata</code>).
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-slate-500">
          {records.length} VM record{records.length !== 1 ? "s" : ""} — edit Owner, Created date, and Purpose inline.
        </p>
        <input type="text"
               placeholder="Search VMs, owners, purposes…"
               value={search}
               onChange={e => setSearch(e.target.value)}
               className="border border-slate-300 rounded-lg px-3 py-1.5 text-sm w-64
                 focus:outline-none focus:ring-2 focus:ring-blue-400" />
      </div>

      <div className="bg-white rounded-xl border border-slate-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-100">
            <thead className="bg-slate-50">
              <tr>
                {["VM Name", "IP Address", "Hypervisor", "Owner", "Created", "Purpose", ""].map(h => (
                  <th key={h} className="px-3 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500 whitespace-nowrap">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-50">
              {displayed.map(r => (
                <VMMetadataEditor key={r.vm_id} record={r} onSaved={load} />
              ))}
            </tbody>
          </table>
        </div>
        <div className="px-5 py-3 border-t border-slate-100 text-xs text-slate-400">
          Showing {displayed.length} of {records.length} records
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function ManageServers() {
  const [tab, setTab]                   = useState("servers"); // "servers" | "metadata"
  const [servers, setServers]           = useState([]);
  const [loading, setLoading]           = useState(true);
  const [showModal, setShowModal]       = useState(false);
  const [editTarget, setEditTarget]     = useState(null);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [probingId, setProbingId]       = useState(null);  // server_id being probed
  const [toast, setToast]               = useState({ msg: "", type: "ok" });

  const load = useCallback(async () => {
    try {
      const res = await axios.get(`${API}/api/servers/config`);
      setServers(res.data);
    } catch { /* silently ignore */ }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  // Auto-refresh every 6 s so probe results appear without manual reload
  useEffect(() => {
    const t = setInterval(load, 6000);
    return () => clearInterval(t);
  }, [load]);

  const showToast = (msg, type = "ok") => {
    setToast({ msg, type });
    setTimeout(() => setToast({ msg: "", type: "ok" }), 4000);
  };

  const handleDelete = async () => {
    await axios.delete(`${API}/api/servers/config/${deleteTarget.server_id}`);
    const name = deleteTarget.display_name;
    setDeleteTarget(null);
    showToast(`"${name}" removed.`);
    load();
  };

  // Uses the new PATCH /toggle endpoint — no need to send the full payload
  const handleToggle = async (server_id) => {
    const s = servers.find(x => x.server_id === server_id);
    try {
      await axios.patch(`${API}/api/servers/config/${server_id}/toggle`);
      showToast(`"${s?.display_name}" ${s?.enabled ? "disabled" : "enabled"}.`);
    } catch {
      showToast("Toggle failed.", "error");
    }
    load();
  };

  const handleProbe = async (server_id) => {
    setProbingId(server_id);
    try {
      const res = await axios.post(`${API}/api/servers/probe/${server_id}`);
      const r = res.data;
      if (r.probe_status === "ok") {
        showToast(`✓ Detected: ${r.cpu_cores} CPUs · ${r.ram_total_gb} GB RAM · ${r.storage_total_tb} TB disk`);
      } else {
        showToast(r.message || "Detection completed.", "warn");
      }
    } catch (e) {
      showToast("Probe request failed. Check backend logs.", "error");
    } finally {
      setProbingId(null);
      load();
    }
  };

  const enabledCount = servers.filter(s => s.enabled).length;
  const pendingCount = servers.filter(s => s.probe_status === "pending").length;

  const TOAST_STYLE = {
    ok:    "bg-emerald-600",
    warn:  "bg-amber-500",
    error: "bg-red-600",
  };

  return (
    <div className="flex flex-col gap-6">

      {/* Page header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-slate-800">Manage Servers</h1>
          <p className="text-sm text-slate-400 mt-0.5">
            {servers.length} host{servers.length !== 1 ? "s" : ""} · {enabledCount} enabled
            {pendingCount > 0 && <span className="ml-2 text-amber-500">· {pendingCount} pending detection</span>}
          </p>
        </div>
        <button onClick={() => { setEditTarget(null); setShowModal(true); }}
                className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white
                  text-sm font-semibold px-5 py-2.5 rounded-xl transition-colors self-start sm:self-auto">
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4"/>
          </svg>
          Add Server
        </button>
      </div>

      {/* Sub-tabs */}
      <div className="flex gap-1 border-b border-slate-200">
        {[
          { id: "servers",  label: "Hypervisor Hosts" },
          { id: "metadata", label: "VM Metadata" },
        ].map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
                  className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors -mb-px ${
                    tab === t.id
                      ? "border-blue-500 text-blue-700"
                      : "border-transparent text-slate-500 hover:text-slate-700"
                  }`}>
            {t.label}
          </button>
        ))}
      </div>

      {/* ── Servers Tab ───────────────────────────────────────────────────────── */}
      {tab === "servers" && (
        loading ? (
          <div className="py-20 flex items-center justify-center">
            <svg className="animate-spin h-7 w-7 text-blue-500" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
            </svg>
          </div>
        ) : servers.length === 0 ? (
          <div className="py-20 text-center">
            <p className="text-slate-500 font-medium">No servers configured yet.</p>
            <p className="text-sm text-slate-400 mt-1">Click "Add Server" to register your first hypervisor host.</p>
          </div>
        ) : (
          <div className="flex flex-col gap-4">
            {servers.map(s => (
              <ServerRow
                key={s.server_id}
                server={s}
                onEdit={srv => { setEditTarget(srv); setShowModal(true); }}
                onDelete={srv => setDeleteTarget(srv)}
                onToggle={handleToggle}
                onProbe={handleProbe}
                probing={probingId === s.server_id}
              />
            ))}
          </div>
        )
      )}

      {/* ── VM Metadata Tab ───────────────────────────────────────────────────── */}
      {tab === "metadata" && <VMMetadataTab />}

      {/* ── Modals ────────────────────────────────────────────────────────────── */}
      {showModal && (
        <ServerModal
          editing={editTarget}
          onClose={() => { setShowModal(false); setEditTarget(null); }}
          onSaved={load}
        />
      )}
      {deleteTarget && (
        <ConfirmDelete
          server={deleteTarget}
          onConfirm={handleDelete}
          onCancel={() => setDeleteTarget(null)}
        />
      )}

      {/* ── Toast ─────────────────────────────────────────────────────────────── */}
      {toast.msg && (
        <div className={`fixed bottom-6 left-1/2 -translate-x-1/2 z-50 px-5 py-3 rounded-xl shadow-lg
          text-white text-sm font-medium ${TOAST_STYLE[toast.type] ?? TOAST_STYLE.ok}`}>
          {toast.msg}
        </div>
      )}
    </div>
  );
}
