/**
 * EventsLog.jsx — Audit log / activity feed tab.
 *
 * Features:
 *  • Summary cards: total events, by-severity counts, top actor
 *  • Filter bar: category, severity, actor, free-text search, date range
 *  • Paginated table with colour-coded severity rows
 *  • Auto-refresh toggle (every 30 s)
 *  • Purge button for events_write users
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import axios from "axios";
import { useAuth } from "./AuthContext";

// ── Colour helpers ─────────────────────────────────────────────────────────────

const SEV_STYLES = {
  info:    { row: "",                       badge: "bg-blue-900/40 text-blue-300 border-blue-700" },
  warning: { row: "bg-amber-900/10",        badge: "bg-amber-900/40 text-amber-300 border-amber-700" },
  error:   { row: "bg-red-900/10",          badge: "bg-red-900/40 text-red-300 border-red-700"   },
};

const CAT_BADGE = {
  auth:    "bg-purple-900/40 text-purple-300 border-purple-700",
  servers: "bg-cyan-900/40   text-cyan-300   border-cyan-700",
  vms:     "bg-teal-900/40   text-teal-300   border-teal-700",
  email:   "bg-indigo-900/40 text-indigo-300 border-indigo-700",
  users:   "bg-pink-900/40   text-pink-300   border-pink-700",
  system:  "bg-slate-700     text-slate-300  border-slate-600",
};

function Badge({ children, cls }) {
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium border ${cls}`}>
      {children}
    </span>
  );
}

// ── Stat card ─────────────────────────────────────────────────────────────────

function StatCard({ label, value, sub, color = "blue" }) {
  const colors = {
    blue:   "border-blue-700  text-blue-400",
    amber:  "border-amber-700 text-amber-400",
    red:    "border-red-700   text-red-400",
    green:  "border-green-700 text-green-400",
    slate:  "border-slate-600 text-slate-400",
  };
  return (
    <div className={`bg-slate-800 border rounded-xl px-5 py-4 ${colors[color]}`}>
      <p className="text-xs font-medium text-slate-400 uppercase tracking-wider">{label}</p>
      <p className={`text-2xl font-bold mt-1 ${colors[color].split(" ")[1]}`}>{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-0.5">{sub}</p>}
    </div>
  );
}

// ── Filter bar ────────────────────────────────────────────────────────────────

const CATEGORIES = ["", "auth", "servers", "vms", "email", "users", "system"];
const SEVERITIES = ["", "info", "warning", "error"];

function FilterBar({ filters, onChange, onClear }) {
  return (
    <div className="bg-slate-800 border border-slate-700 rounded-xl px-5 py-4
                    grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      {/* Category */}
      <div>
        <label className="block text-xs text-slate-400 mb-1">Category</label>
        <select value={filters.category}
                onChange={e => onChange("category", e.target.value)}
                className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg
                           px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
          {CATEGORIES.map(c => <option key={c} value={c}>{c || "All"}</option>)}
        </select>
      </div>
      {/* Severity */}
      <div>
        <label className="block text-xs text-slate-400 mb-1">Severity</label>
        <select value={filters.severity}
                onChange={e => onChange("severity", e.target.value)}
                className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg
                           px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
          {SEVERITIES.map(s => <option key={s} value={s}>{s || "All"}</option>)}
        </select>
      </div>
      {/* Actor */}
      <div>
        <label className="block text-xs text-slate-400 mb-1">Actor</label>
        <input type="text" value={filters.actor} placeholder="username"
               onChange={e => onChange("actor", e.target.value)}
               className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg
                          px-2 py-1.5 text-sm placeholder-slate-500
                          focus:outline-none focus:ring-2 focus:ring-blue-500" />
      </div>
      {/* Search */}
      <div>
        <label className="block text-xs text-slate-400 mb-1">Search</label>
        <input type="text" value={filters.search} placeholder="detail / target"
               onChange={e => onChange("search", e.target.value)}
               className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg
                          px-2 py-1.5 text-sm placeholder-slate-500
                          focus:outline-none focus:ring-2 focus:ring-blue-500" />
      </div>
      {/* Since */}
      <div>
        <label className="block text-xs text-slate-400 mb-1">From</label>
        <input type="date" value={filters.since}
               onChange={e => onChange("since", e.target.value)}
               className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg
                          px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
      </div>
      {/* Until */}
      <div>
        <label className="block text-xs text-slate-400 mb-1">To</label>
        <input type="date" value={filters.until}
               onChange={e => onChange("until", e.target.value)}
               className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg
                          px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
      </div>
      {/* Clear button spanning full width */}
      <div className="col-span-full flex justify-end">
        <button onClick={onClear}
                className="text-xs text-slate-400 hover:text-slate-200 transition">
          Clear filters
        </button>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

const BLANK_FILTERS = { category: "", severity: "", actor: "", search: "", since: "", until: "" };

export default function EventsLog() {
  const { permissions } = useAuth();
  const canWrite = !!permissions?.events_write;

  const [page,      setPage]      = useState(1);
  const [data,      setData]      = useState(null);   // EventsPage
  const [stats,     setStats]     = useState(null);   // EventStats
  const [filters,   setFilters]   = useState(BLANK_FILTERS);
  const [loading,   setLoading]   = useState(true);
  const [autoRef,   setAutoRef]   = useState(false);
  const [purging,   setPurging]   = useState(false);
  const [purgeDays, setPurgeDays] = useState(90);
  const [toast,     setToast]     = useState("");
  const timerRef = useRef(null);

  const showToast = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(""), 4000);
  };

  const buildParams = useCallback(() => {
    const p = { page, page_size: 50 };
    if (filters.category) p.category = filters.category;
    if (filters.severity) p.severity = filters.severity;
    if (filters.actor)    p.actor    = filters.actor;
    if (filters.search)   p.search   = filters.search;
    if (filters.since)    p.since    = filters.since;
    if (filters.until)    p.until    = filters.until;
    return p;
  }, [page, filters]);

  const loadEvents = useCallback(async () => {
    try {
      const [evts, st] = await Promise.all([
        axios.get("/api/events",       { params: buildParams() }),
        axios.get("/api/events/stats"),
      ]);
      setData(evts.data);
      setStats(st.data);
    } catch { /* handled by axios 401 interceptor */ }
    finally { setLoading(false); }
  }, [buildParams]);

  // Reload whenever page/filters change
  useEffect(() => {
    setLoading(true);
    loadEvents();
  }, [loadEvents]);

  // Auto-refresh
  useEffect(() => {
    if (autoRef) {
      timerRef.current = setInterval(loadEvents, 30_000);
    } else {
      clearInterval(timerRef.current);
    }
    return () => clearInterval(timerRef.current);
  }, [autoRef, loadEvents]);

  const setFilter = (key, val) => {
    setFilters(f => ({ ...f, [key]: val }));
    setPage(1);
  };

  const clearFilters = () => { setFilters(BLANK_FILTERS); setPage(1); };

  const doPurge = async () => {
    if (!window.confirm(`Delete all events older than ${purgeDays} days?`)) return;
    setPurging(true);
    try {
      const { data: r } = await axios.delete("/api/events", {
        params: { older_than_days: purgeDays },
      });
      showToast(r.detail);
      loadEvents();
    } catch (err) {
      showToast(err?.response?.data?.detail || "Purge failed.");
    } finally { setPurging(false); }
  };

  const fmtTs = (iso) => {
    if (!iso) return "—";
    const d = new Date(iso + "Z");    // treat as UTC
    return d.toLocaleString(undefined, {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
    });
  };

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div className="space-y-6">
      {/* Toast */}
      {toast && (
        <div className="fixed top-4 right-4 z-50 bg-slate-700 border border-slate-600
                        text-slate-200 rounded-lg px-4 py-3 shadow-xl text-sm">
          {toast}
        </div>
      )}

      {/* Header row */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-white">Events &amp; Audit Log</h1>
          <p className="text-sm text-slate-400 mt-0.5">
            Full history of all user actions and system events.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* Auto-refresh toggle */}
          <label className="flex items-center gap-2 cursor-pointer text-sm text-slate-400">
            <input type="checkbox" checked={autoRef}
                   className="accent-blue-500 w-4 h-4"
                   onChange={e => setAutoRef(e.target.checked)} />
            Auto-refresh (30 s)
          </label>
          {/* Manual refresh */}
          <button onClick={loadEvents}
                  className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-slate-700
                             hover:bg-slate-600 text-slate-200 rounded-lg border border-slate-600 transition">
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round"
                    d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
            Refresh
          </button>
        </div>
      </div>

      {/* Stats cards */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <StatCard label="Total Events"   value={stats.total.toLocaleString()} color="blue" />
          <StatCard label="Warnings"
                    value={(stats.by_severity?.warning ?? 0).toLocaleString()}
                    color="amber" />
          <StatCard label="Errors"
                    value={(stats.by_severity?.error ?? 0).toLocaleString()}
                    color="red" />
          <StatCard label="Top Actor"
                    value={stats.by_actor?.[0]?.actor ?? "—"}
                    sub={stats.by_actor?.[0] ? `${stats.by_actor[0].count} events` : ""}
                    color="slate" />
        </div>
      )}

      {/* Filters */}
      <FilterBar filters={filters} onChange={setFilter} onClear={clearFilters} />

      {/* Table */}
      <div className="bg-slate-800 border border-slate-700 rounded-xl overflow-hidden">

        {/* Table header */}
        <div className="px-5 py-3 border-b border-slate-700 flex items-center justify-between">
          <span className="text-sm text-slate-400">
            {loading ? "Loading…" : `${data?.total?.toLocaleString() ?? 0} events`}
            {data && data.pages > 1 && ` — page ${data.page} of ${data.pages}`}
          </span>
          {/* Purge control */}
          {canWrite && (
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500">Purge older than</span>
              <input type="number" min={1} max={3650} value={purgeDays}
                     onChange={e => setPurgeDays(Number(e.target.value))}
                     className="w-16 bg-slate-700 border border-slate-600 text-white rounded px-2 py-1
                                text-xs focus:outline-none focus:ring-1 focus:ring-red-500" />
              <span className="text-xs text-slate-500">days</span>
              <button onClick={doPurge} disabled={purging}
                      className="px-3 py-1 text-xs bg-red-800 hover:bg-red-700 text-red-200
                                 rounded border border-red-700 disabled:opacity-50 transition">
                {purging ? "Purging…" : "Purge"}
              </button>
            </div>
          )}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead className="bg-slate-700/60 text-slate-400 text-xs uppercase">
              <tr>
                {["Time (UTC)","Actor","IP","Category","Action","Target","Detail","Severity"].map(h => (
                  <th key={h} className="px-4 py-3 whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-700/40">
              {loading ? (
                <tr><td colSpan={8} className="px-4 py-8 text-center text-slate-500">Loading…</td></tr>
              ) : !data?.items?.length ? (
                <tr><td colSpan={8} className="px-4 py-8 text-center text-slate-600">No events found.</td></tr>
              ) : data.items.map(ev => {
                const sev = SEV_STYLES[ev.severity] ?? SEV_STYLES.info;
                return (
                  <tr key={ev.id} className={`hover:bg-slate-700/30 transition ${sev.row}`}>
                    <td className="px-4 py-2.5 text-slate-400 whitespace-nowrap font-mono text-xs">
                      {fmtTs(ev.ts)}
                    </td>
                    <td className="px-4 py-2.5 font-medium text-white whitespace-nowrap">
                      {ev.actor}
                    </td>
                    <td className="px-4 py-2.5 text-slate-500 font-mono text-xs whitespace-nowrap">
                      {ev.actor_ip || "—"}
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      <Badge cls={CAT_BADGE[ev.category] ?? CAT_BADGE.system}>
                        {ev.category}
                      </Badge>
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-slate-300 whitespace-nowrap">
                      {ev.action}
                    </td>
                    <td className="px-4 py-2.5 text-slate-400 text-xs max-w-[140px] truncate"
                        title={ev.target}>
                      {ev.target || "—"}
                    </td>
                    <td className="px-4 py-2.5 text-slate-300 text-xs max-w-[300px] truncate"
                        title={ev.detail}>
                      {ev.detail || "—"}
                    </td>
                    <td className="px-4 py-2.5 whitespace-nowrap">
                      <Badge cls={sev.badge}>{ev.severity}</Badge>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {data && data.pages > 1 && (
          <div className="flex items-center justify-between px-5 py-3 border-t border-slate-700">
            <button disabled={page <= 1}
                    onClick={() => setPage(p => p - 1)}
                    className="px-3 py-1.5 text-sm bg-slate-700 hover:bg-slate-600 text-slate-200
                               rounded border border-slate-600 disabled:opacity-40 transition">
              ← Previous
            </button>
            <span className="text-sm text-slate-400">
              Page {data.page} / {data.pages}
            </span>
            <button disabled={page >= data.pages}
                    onClick={() => setPage(p => p + 1)}
                    className="px-3 py-1.5 text-sm bg-slate-700 hover:bg-slate-600 text-slate-200
                               rounded border border-slate-600 disabled:opacity-40 transition">
              Next →
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
