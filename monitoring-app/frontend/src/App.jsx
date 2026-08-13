/**
 * App.jsx — Root application shell — HyperMonitor v4.0
 *
 * Changes in v4:
 *  • Wrapped in <AuthProvider> so every component can call useAuth()
 *  • Shows <Login> when the user is not authenticated
 *  • 4th nav tab "Users & Groups" visible only when permissions.users_view
 *  • User badge (username + groups) in the header
 *  • Logout button in the header
 *  • Change-password modal in the header menu
 */
import React, { useState } from "react";
import axios from "axios";
import { AuthProvider, useAuth } from "./AuthContext";
import Login         from "./Login";
import ForceChangePw from "./ForceChangePw";
import Dashboard     from "./Dashboard";
import ManageServers from "./ManageServers";
import EmailSettings from "./EmailSettings";
import UserAdmin     from "./UserAdmin";
import EventsLog     from "./EventsLog";

// ── Navigation definition ─────────────────────────────────────────────────────

const NAV_BASE = [
  {
    id: "dashboard",
    label: "Dashboard",
    perm: "dashboard_view",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round"
              d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
      </svg>
    ),
  },
  {
    id: "servers",
    label: "Manage Servers",
    perm: "servers_view",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round"
              d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2M9 7h.01M9 17h.01" />
      </svg>
    ),
  },
  {
    id: "email",
    label: "Email Reports",
    perm: "email_view",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round"
              d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
      </svg>
    ),
  },
  {
    id: "users",
    label: "Users & Groups",
    perm: "users_view",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round"
              d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z" />
      </svg>
    ),
  },
  {
    id: "events",
    label: "Events",
    perm: "events_view",
    icon: (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round"
              d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
      </svg>
    ),
  },
];

// ── Change-password modal ─────────────────────────────────────────────────────

function ChangePwModal({ onClose }) {
  const [cur,   setCur]   = useState("");
  const [next,  setNext]  = useState("");
  const [msg,   setMsg]   = useState("");
  const [ok,    setOk]    = useState(false);
  const [busy,  setBusy]  = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    if (next.length < 8) { setMsg("New password must be at least 8 characters."); return; }
    setBusy(true);
    setMsg("");
    try {
      await axios.post("/api/auth/change-password", {
        current_password: cur,
        new_password:     next,
      });
      setOk(true);
      setMsg("Password changed successfully.");
    } catch (err) {
      setMsg(err?.response?.data?.detail || "Failed to change password.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-slate-800 border border-slate-700 rounded-2xl shadow-2xl w-full max-w-sm">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-700">
          <h3 className="font-semibold text-white">Change Password</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-white text-xl leading-none">✕</button>
        </div>
        <form onSubmit={submit} className="px-6 py-5 space-y-4">
          {msg && (
            <p className={`text-sm rounded-lg px-3 py-2 ${ok
              ? "bg-green-900/40 text-green-300 border border-green-700"
              : "bg-red-900/40  text-red-300   border border-red-700"}`}>
              {msg}
            </p>
          )}
          <div>
            <label className="block text-xs text-slate-400 mb-1">Current Password</label>
            <input type="password" value={cur} onChange={e => setCur(e.target.value)} required
                   className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2
                              text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div>
            <label className="block text-xs text-slate-400 mb-1">New Password</label>
            <input type="password" value={next} onChange={e => setNext(e.target.value)} required
                   className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2
                              text-sm focus:outline-none focus:ring-2 focus:ring-blue-500" />
          </div>
          <div className="flex justify-end gap-3 pt-1">
            <button type="button" onClick={onClose}
                    className="px-4 py-2 text-sm rounded-lg bg-slate-700 hover:bg-slate-600 text-slate-200 border border-slate-600">
              {ok ? "Close" : "Cancel"}
            </button>
            {!ok && (
              <button type="submit" disabled={busy}
                      className="px-4 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-500
                                 text-white disabled:opacity-50">
                {busy ? "Saving…" : "Change Password"}
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Main shell (rendered only when authenticated) ─────────────────────────────

function Shell() {
  const { user, permissions, logout, mustChangePw } = useAuth();
  const [tab,        setTab]        = useState(null);
  const [userMenu,   setUserMenu]   = useState(false);
  const [changePwOpen, setChangePwOpen] = useState(false);

  // Build visible nav based on permissions
  const visibleNav = NAV_BASE.filter(n => permissions?.[n.perm]);
  // Set default tab to first permitted nav item
  const activeTab  = tab && visibleNav.find(n => n.id === tab)
    ? tab
    : visibleNav[0]?.id ?? "dashboard";

  const handleLogout = async () => {
    setUserMenu(false);
    await logout();
  };

  return (
    <div className="min-h-screen bg-slate-100 font-sans">

      {/* ── Forced password change — blocks entire UI until completed ──────── */}
      {mustChangePw && <ForceChangePw />}

      {/* ── Top Navigation Bar ───────────────────────────────────────────── */}
      <header className="bg-slate-900 text-white shadow-lg sticky top-0 z-40">
        <div className="max-w-screen-2xl mx-auto px-4 sm:px-6 py-0 flex items-stretch">

          {/* Brand */}
          <div className="flex items-center gap-3 pr-6 py-4 border-r border-slate-700 shrink-0">
            <svg className="w-6 h-6 text-blue-400 shrink-0" fill="none" viewBox="0 0 24 24"
                 stroke="currentColor" strokeWidth={1.8}>
              <path strokeLinecap="round" strokeLinejoin="round"
                    d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2" />
            </svg>
            <span className="text-lg font-bold tracking-tight whitespace-nowrap">HyperMonitor</span>
          </div>

          {/* Tab nav */}
          <nav className="flex items-stretch gap-1 px-4 flex-1 overflow-x-auto">
            {visibleNav.map(({ id, label, icon }) => (
              <button
                key={id}
                onClick={() => setTab(id)}
                className={`flex items-center gap-2 px-4 py-4 text-sm font-medium border-b-2
                             transition-colors whitespace-nowrap
                  ${activeTab === id
                    ? "border-blue-400 text-white"
                    : "border-transparent text-slate-400 hover:text-slate-200 hover:border-slate-500"
                  }`}
              >
                {icon}
                {label}
              </button>
            ))}
          </nav>

          {/* User menu */}
          <div className="flex items-center pl-4 border-l border-slate-700 relative shrink-0">
            <button
              onClick={() => setUserMenu(v => !v)}
              className="flex items-center gap-2 px-3 py-2 rounded-lg hover:bg-slate-800 transition"
            >
              {/* Avatar circle */}
              <span className="w-8 h-8 rounded-full bg-blue-600 flex items-center justify-center
                               text-white text-sm font-bold uppercase shrink-0">
                {user?.username?.[0] ?? "?"}
              </span>
              <div className="hidden sm:block text-left">
                <p className="text-sm font-medium text-white leading-tight">{user?.username}</p>
                <p className="text-xs text-slate-400 leading-tight">
                  {user?.group_names?.join(", ") || (user?.is_root ? "root" : "—")}
                </p>
              </div>
              {/* Chevron */}
              <svg className={`w-4 h-4 text-slate-400 transition-transform ${userMenu ? "rotate-180" : ""}`}
                   fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {/* Dropdown */}
            {userMenu && (
              <div className="absolute top-full right-0 mt-1 w-52 bg-slate-800 border border-slate-700
                              rounded-xl shadow-2xl z-50 py-1">
                <div className="px-4 py-3 border-b border-slate-700">
                  <p className="text-sm font-semibold text-white">{user?.username}</p>
                  <p className="text-xs text-slate-400 truncate">{user?.email || "No email"}</p>
                </div>
                <button
                  onClick={() => { setUserMenu(false); setChangePwOpen(true); }}
                  className="w-full text-left px-4 py-2.5 text-sm text-slate-300
                             hover:bg-slate-700 transition"
                >
                  Change Password
                </button>
                <button
                  onClick={handleLogout}
                  className="w-full text-left px-4 py-2.5 text-sm text-red-400
                             hover:bg-slate-700 transition"
                >
                  Sign Out
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {/* Click-outside to close user menu */}
      {userMenu && (
        <div className="fixed inset-0 z-30" onClick={() => setUserMenu(false)} />
      )}

      {/* ── Change-password modal ────────────────────────────────────────── */}
      {changePwOpen && <ChangePwModal onClose={() => setChangePwOpen(false)} />}

      {/* ── Page content ─────────────────────────────────────────────────── */}
      <main className="max-w-screen-2xl mx-auto px-4 sm:px-6 py-8">
        {activeTab === "dashboard" && (
          <Dashboard
            onGoToServers={() => setTab("servers")}
            onGoToEmail={() => setTab("email")}
          />
        )}
        {activeTab === "servers" && <ManageServers />}
        {activeTab === "email"   && <EmailSettings />}
        {activeTab === "users"   && <UserAdmin />}
        {activeTab === "events"  && <EventsLog />}
      </main>
    </div>
  );
}

// ── Root component — wraps everything in AuthProvider ─────────────────────────

export default function App() {
  return (
    <AuthProvider>
      <AppGate />
    </AuthProvider>
  );
}

/**
 * AppGate — decides whether to show Login or Shell.
 * Must be rendered inside <AuthProvider> so useAuth() works.
 */
function AppGate() {
  const { isAuthenticated } = useAuth();
  return isAuthenticated ? <Shell /> : <Login />;
}
