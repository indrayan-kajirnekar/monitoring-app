/**
 * UserAdmin.jsx — Users & Groups management page.
 *
 * Four inner tabs:
 *   Users       – list of all users with edit/delete/reset-password
 *   Create User – form to add a new user
 *   Groups      – list of all groups with edit/delete
 *   Create Group – form to add a new group with permission checkboxes
 *
 * Requires permission: users_view (read tabs visible) / users_write (mutations)
 */
import React, { useCallback, useEffect, useState } from "react";
import axios from "axios";
import { useAuth } from "./AuthContext";

// ── Permission key metadata ───────────────────────────────────────────────────
const PERM_KEYS = [
  { key: "dashboard_view",  label: "Dashboard",     sub: "View" },
  { key: "dashboard_write", label: "Dashboard",     sub: "Write" },
  { key: "servers_view",    label: "Manage Servers",sub: "View" },
  { key: "servers_write",   label: "Manage Servers",sub: "Write" },
  { key: "email_view",      label: "Email Reports", sub: "View" },
  { key: "email_write",     label: "Email Reports", sub: "Write" },
  { key: "users_view",      label: "Users & Groups",sub: "View" },
  { key: "users_write",     label: "Users & Groups",sub: "Write" },
  { key: "events_view",     label: "Events Log",    sub: "View" },
  { key: "events_write",    label: "Events Log",    sub: "Purge" },
];

// ── Small reusable pieces ─────────────────────────────────────────────────────

function Badge({ children, color = "slate" }) {
  const map = {
    green:  "bg-green-900/50 text-green-300 border border-green-700",
    red:    "bg-red-900/50  text-red-300   border border-red-700",
    blue:   "bg-blue-900/50 text-blue-300  border border-blue-700",
    purple: "bg-purple-900/50 text-purple-300 border border-purple-700",
    slate:  "bg-slate-700   text-slate-300  border border-slate-600",
  };
  return (
    <span className={`inline-block rounded px-1.5 py-0.5 text-xs font-medium ${map[color]}`}>
      {children}
    </span>
  );
}

function Toast({ msg, type, onClose }) {
  if (!msg) return null;
  const bg = type === "error" ? "bg-red-800 border-red-600 text-red-200"
                               : "bg-green-800 border-green-600 text-green-200";
  return (
    <div className={`fixed top-4 right-4 z-50 flex items-center gap-3 border rounded-lg px-4 py-3 shadow-xl ${bg}`}>
      <span className="text-sm">{msg}</span>
      <button onClick={onClose} className="text-current opacity-60 hover:opacity-100">✕</button>
    </div>
  );
}

function SectionTitle({ children }) {
  return <h2 className="text-lg font-semibold text-white mb-4">{children}</h2>;
}

function Input({ label, ...props }) {
  return (
    <div>
      {label && <label className="block text-xs font-medium text-slate-400 mb-1">{label}</label>}
      <input
        {...props}
        className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2
                   text-sm placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500 transition"
      />
    </div>
  );
}

function Select({ label, children, ...props }) {
  return (
    <div>
      {label && <label className="block text-xs font-medium text-slate-400 mb-1">{label}</label>}
      <select
        {...props}
        className="w-full bg-slate-700 border border-slate-600 text-white rounded-lg px-3 py-2
                   text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 transition"
      >
        {children}
      </select>
    </div>
  );
}

function Btn({ children, variant = "primary", size = "md", ...props }) {
  const base = "rounded-lg font-medium transition disabled:opacity-50 disabled:cursor-not-allowed";
  const sz   = size === "sm" ? "px-3 py-1 text-xs" : "px-4 py-2 text-sm";
  const col  = {
    primary: "bg-blue-600 hover:bg-blue-500 text-white",
    danger:  "bg-red-700  hover:bg-red-600  text-white",
    ghost:   "bg-slate-700 hover:bg-slate-600 text-slate-200 border border-slate-600",
  }[variant];
  return <button className={`${base} ${sz} ${col}`} {...props}>{children}</button>;
}

// ── Permission Checkbox Grid ──────────────────────────────────────────────────

function PermGrid({ perms, onChange, disabled = false }) {
  // Group by section label
  const sections = {};
  for (const p of PERM_KEYS) {
    sections[p.label] = sections[p.label] ?? [];
    sections[p.label].push(p);
  }
  return (
    <div className="grid grid-cols-2 gap-3">
      {Object.entries(sections).map(([section, items]) => (
        <div key={section} className="bg-slate-700/50 rounded-lg px-4 py-3 border border-slate-600">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">{section}</p>
          <div className="space-y-1.5">
            {items.map(({ key, sub }) => (
              <label key={key} className="flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={!!perms[key]}
                  disabled={disabled}
                  onChange={(e) => onChange(key, e.target.checked)}
                  className="accent-blue-500 w-4 h-4 cursor-pointer"
                />
                <span className="text-sm text-slate-300">{sub}</span>
              </label>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

// ── USERS tab ─────────────────────────────────────────────────────────────────

function UsersTab({ canWrite, groups, showToast }) {
  const [users, setUsers]     = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null);   // user object being edited
  const [resetting, setResetting] = useState(null); // user object for pw reset
  const [newPw, setNewPw]       = useState("");
  const [forceChangePw, setForceChangePw] = useState(true); // default: require change

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get("/api/users");
      setUsers(data);
    } catch { showToast("Failed to load users.", "error"); }
    finally   { setLoading(false); }
  }, [showToast]);

  useEffect(() => { load(); }, [load]);

  const saveEdit = async () => {
    try {
      await axios.put(`/api/users/${editing.id}`, {
        full_name: editing.full_name,
        email:     editing.email,
        is_active: editing.is_active,
        group_ids: editing.group_ids,
      });
      showToast("User updated.", "success");
      setEditing(null);
      load();
    } catch (err) {
      showToast(err?.response?.data?.detail || "Update failed.", "error");
    }
  };

  const doDelete = async (u) => {
    if (!window.confirm(`Delete user "${u.username}"? This cannot be undone.`)) return;
    try {
      await axios.delete(`/api/users/${u.id}`);
      showToast(`User "${u.username}" deleted.`, "success");
      load();
    } catch (err) {
      showToast(err?.response?.data?.detail || "Delete failed.", "error");
    }
  };

  const doReset = async () => {
    if (!newPw || newPw.length < 8) { showToast("Password must be ≥8 characters.", "error"); return; }
    try {
      await axios.post(`/api/users/${resetting.id}/reset-password`, {
        new_password:         newPw,
        must_change_password: forceChangePw,
      });
      showToast("Password reset successfully.", "success");
      setResetting(null);
      setNewPw("");
      load();
    } catch (err) {
      showToast(err?.response?.data?.detail || "Reset failed.", "error");
    }
  };

  if (loading) return <p className="text-slate-400 text-sm">Loading users…</p>;

  return (
    <div>
      <SectionTitle>All Users ({users.length})</SectionTitle>

      {/* User table */}
      <div className="overflow-x-auto rounded-xl border border-slate-700">
        <table className="w-full text-sm text-left">
          <thead className="bg-slate-700/60 text-slate-400 text-xs uppercase">
            <tr>
              {["Username","Full Name","Email","Groups","Status","Pwd Reset","Last Login","Actions"].map(h => (
                <th key={h} className="px-4 py-3 whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-700/50">
            {users.map((u) => (
              <tr key={u.id} className="hover:bg-slate-700/30 transition">
                <td className="px-4 py-3 font-medium text-white whitespace-nowrap">
                  <div className="flex items-center gap-1.5">
                    {u.username}
                    {u.is_root && <Badge color="purple">root</Badge>}
                  </div>
                </td>
                <td className="px-4 py-3 text-slate-300">{u.full_name || "—"}</td>
                <td className="px-4 py-3 text-slate-400">{u.email || "—"}</td>
                <td className="px-4 py-3">
                  <div className="flex flex-wrap gap-1">
                    {u.group_names.length > 0
                      ? u.group_names.map(n => <Badge key={n} color="blue">{n}</Badge>)
                      : <span className="text-slate-500">—</span>}
                  </div>
                </td>
                <td className="px-4 py-3">
                  <Badge color={u.is_active ? "green" : "red"}>
                    {u.is_active ? "Active" : "Disabled"}
                  </Badge>
                </td>
                {/* must_change_password indicator */}
                <td className="px-4 py-3">
                  {u.must_change_password
                    ? <Badge color="red">Required</Badge>
                    : <span className="text-slate-600 text-xs">—</span>}
                </td>
                <td className="px-4 py-3 text-slate-500 whitespace-nowrap">
                  {u.last_login_at
                    ? new Date(u.last_login_at).toLocaleString()
                    : "Never"}
                </td>
                <td className="px-4 py-3">
                  {/* Root: show only reset-password (only root itself or no one can) */}
                  {canWrite && u.is_root && (
                    <Btn size="sm" variant="ghost"
                         onClick={() => { setResetting(u); setNewPw(""); setForceChangePw(false); }}>
                      Reset Password
                    </Btn>
                  )}
                  {canWrite && !u.is_root && (
                    <div className="flex gap-2">
                      <Btn size="sm" variant="ghost" onClick={() => setEditing({ ...u })}>Edit</Btn>
                      <Btn size="sm" variant="ghost"
                           onClick={() => { setResetting(u); setNewPw(""); setForceChangePw(true); }}>
                        Reset PW
                      </Btn>
                      <Btn size="sm" variant="danger" onClick={() => doDelete(u)}>Delete</Btn>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Edit modal */}
      {editing && (
        <Modal title={`Edit User: ${editing.username}`} onClose={() => setEditing(null)}>
          <div className="space-y-4">
            <Input label="Full Name" value={editing.full_name}
                   onChange={(e) => setEditing({ ...editing, full_name: e.target.value })} />
            <Input label="Email" type="email" value={editing.email}
                   onChange={(e) => setEditing({ ...editing, email: e.target.value })} />
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Groups</label>
              <div className="space-y-1.5">
                {groups.map(g => (
                  <label key={g.id} className="flex items-center gap-2 cursor-pointer">
                    <input type="checkbox"
                           checked={editing.group_ids.includes(g.id)}
                           className="accent-blue-500 w-4 h-4"
                           onChange={(e) => {
                             const gids = e.target.checked
                               ? [...editing.group_ids, g.id]
                               : editing.group_ids.filter(x => x !== g.id);
                             setEditing({ ...editing, group_ids: gids });
                           }} />
                    <span className="text-sm text-slate-300">{g.name}</span>
                  </label>
                ))}
              </div>
            </div>
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={editing.is_active} className="accent-blue-500 w-4 h-4"
                     onChange={(e) => setEditing({ ...editing, is_active: e.target.checked })} />
              <span className="text-sm text-slate-300">Account active</span>
            </label>
            <div className="flex justify-end gap-3 pt-2">
              <Btn variant="ghost" onClick={() => setEditing(null)}>Cancel</Btn>
              <Btn onClick={saveEdit}>Save Changes</Btn>
            </div>
          </div>
        </Modal>
      )}

      {/* Reset-password modal */}
      {resetting && (
        <Modal title={`Reset Password — ${resetting.username}`} onClose={() => setResetting(null)}>
          <div className="space-y-4">
            <Input label="New Password (min 8 chars)" type="password"
                   value={newPw} onChange={(e) => setNewPw(e.target.value)} />
            {/* Force-change checkbox */}
            <label className="flex items-start gap-3 cursor-pointer select-none
                               bg-amber-900/20 border border-amber-700 rounded-lg px-4 py-3">
              <input type="checkbox" checked={forceChangePw}
                     className="accent-amber-500 w-4 h-4 mt-0.5 shrink-0"
                     onChange={(e) => setForceChangePw(e.target.checked)} />
              <div>
                <p className="text-sm font-medium text-amber-200">
                  Require password change at first login
                </p>
                <p className="text-xs text-amber-400 mt-0.5">
                  The user will be forced to set a new password before accessing the app.
                </p>
              </div>
            </label>
            <div className="flex justify-end gap-3 pt-2">
              <Btn variant="ghost" onClick={() => setResetting(null)}>Cancel</Btn>
              <Btn onClick={doReset}>Set Password</Btn>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ── CREATE USER tab ───────────────────────────────────────────────────────────

function CreateUserTab({ groups, showToast }) {
  const blank = {
    username: "", full_name: "", email: "", password: "",
    group_ids: [], is_active: true, must_change_password: false,
  };
  const [form, setForm] = useState(blank);
  const [saving, setSaving] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      await axios.post("/api/users", form);
      showToast(`User "${form.username}" created.`, "success");
      setForm(blank);
    } catch (err) {
      showToast(err?.response?.data?.detail || "Create failed.", "error");
    } finally { setSaving(false); }
  };

  const toggleGroup = (gid, checked) => {
    setForm(f => ({
      ...f,
      group_ids: checked ? [...f.group_ids, gid] : f.group_ids.filter(x => x !== gid),
    }));
  };

  return (
    <div className="max-w-lg">
      <SectionTitle>Create New User</SectionTitle>
      <form onSubmit={submit} className="space-y-4">
        <Input label="Username *" value={form.username} required
               onChange={(e) => setForm(f => ({ ...f, username: e.target.value }))} />
        <Input label="Full Name" value={form.full_name}
               onChange={(e) => setForm(f => ({ ...f, full_name: e.target.value }))} />
        <Input label="Email" type="email" value={form.email}
               onChange={(e) => setForm(f => ({ ...f, email: e.target.value }))} />
        <Input label="Password * (min 8 chars)" type="password" required
               value={form.password}
               onChange={(e) => setForm(f => ({ ...f, password: e.target.value }))} />

        <div>
          <label className="block text-xs font-medium text-slate-400 mb-2">Groups</label>
          <div className="grid grid-cols-2 gap-1.5">
            {groups.map(g => (
              <label key={g.id} className="flex items-center gap-2 cursor-pointer">
                <input type="checkbox" checked={form.group_ids.includes(g.id)}
                       className="accent-blue-500 w-4 h-4"
                       onChange={(e) => toggleGroup(g.id, e.target.checked)} />
                <span className="text-sm text-slate-300">{g.name}</span>
              </label>
            ))}
          </div>
        </div>

        <label className="flex items-center gap-2 cursor-pointer">
          <input type="checkbox" checked={form.is_active} className="accent-blue-500 w-4 h-4"
                 onChange={(e) => setForm(f => ({ ...f, is_active: e.target.checked }))} />
          <span className="text-sm text-slate-300">Account active</span>
        </label>

        {/* Force change password on first login */}
        <label className="flex items-start gap-3 cursor-pointer select-none
                           bg-amber-900/20 border border-amber-700 rounded-lg px-4 py-3">
          <input type="checkbox" checked={form.must_change_password}
                 className="accent-amber-500 w-4 h-4 mt-0.5 shrink-0"
                 onChange={(e) => setForm(f => ({ ...f, must_change_password: e.target.checked }))} />
          <div>
            <p className="text-sm font-medium text-amber-200">
              Require password change at first login
            </p>
            <p className="text-xs text-amber-400 mt-0.5">
              The user will be forced to set a new password before accessing the app.
            </p>
          </div>
        </label>

        <Btn type="submit" disabled={saving}>
          {saving ? "Creating…" : "Create User"}
        </Btn>
      </form>
    </div>
  );
}

// ── GROUPS tab ────────────────────────────────────────────────────────────────

function GroupsTab({ canWrite, showToast, onGroupsChanged }) {
  const [groups, setGroups]   = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get("/api/groups");
      setGroups(data);
      onGroupsChanged(data);
    } catch { showToast("Failed to load groups.", "error"); }
    finally   { setLoading(false); }
  }, [showToast, onGroupsChanged]);

  useEffect(() => { load(); }, [load]);

  const saveEdit = async () => {
    try {
      await axios.put(`/api/groups/${editing.id}`, {
        name:        editing.name,
        description: editing.description,
        permissions: editing.permissions,
      });
      showToast("Group updated.", "success");
      setEditing(null);
      load();
    } catch (err) {
      showToast(err?.response?.data?.detail || "Update failed.", "error");
    }
  };

  const doDelete = async (g) => {
    if (!window.confirm(`Delete group "${g.name}"?`)) return;
    try {
      await axios.delete(`/api/groups/${g.id}`);
      showToast(`Group "${g.name}" deleted.`, "success");
      load();
    } catch (err) {
      showToast(err?.response?.data?.detail || "Delete failed.", "error");
    }
  };

  const setEditPerm = (key, val) =>
    setEditing(ed => ({ ...ed, permissions: { ...ed.permissions, [key]: val } }));

  if (loading) return <p className="text-slate-400 text-sm">Loading groups…</p>;

  return (
    <div>
      <SectionTitle>Permission Groups ({groups.length})</SectionTitle>
      <div className="space-y-3">
        {groups.map((g) => (
          <div key={g.id}
               className="bg-slate-800 border border-slate-700 rounded-xl px-5 py-4 flex flex-col gap-3
                          sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0">
              <div className="flex items-center gap-2 mb-1">
                <span className="font-semibold text-white">{g.name}</span>
                {g.is_builtin && <Badge color="purple">built-in</Badge>}
                <Badge color="slate">{g.member_count} member{g.member_count !== 1 ? "s" : ""}</Badge>
              </div>
              <p className="text-sm text-slate-400">{g.description || "No description."}</p>
              {/* Permission pills */}
              <div className="flex flex-wrap gap-1 mt-2">
                {PERM_KEYS.filter(pk => g.permissions[pk.key])
                  .map(pk => (
                    <span key={pk.key}
                          className="text-xs bg-blue-900/40 text-blue-300 border border-blue-800 rounded px-1.5 py-0.5">
                      {pk.label} {pk.sub}
                    </span>
                  ))}
                {!PERM_KEYS.some(pk => g.permissions[pk.key]) && (
                  <span className="text-xs text-slate-600">No permissions</span>
                )}
              </div>
            </div>
            {canWrite && (
              <div className="flex gap-2 shrink-0">
                <Btn size="sm" variant="ghost" onClick={() => setEditing({ ...g })}>Edit</Btn>
                {!g.is_builtin && (
                  <Btn size="sm" variant="danger" onClick={() => doDelete(g)}>Delete</Btn>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Edit group modal */}
      {editing && (
        <Modal title={`Edit Group: ${editing.name}`} onClose={() => setEditing(null)} wide>
          <div className="space-y-4">
            <Input label="Group Name" value={editing.name}
                   disabled={editing.is_builtin}
                   onChange={(e) => setEditing(ed => ({ ...ed, name: e.target.value }))} />
            <Input label="Description" value={editing.description}
                   onChange={(e) => setEditing(ed => ({ ...ed, description: e.target.value }))} />
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-2">Permissions</label>
              <PermGrid perms={editing.permissions} onChange={setEditPerm} />
            </div>
            <div className="flex justify-end gap-3 pt-2">
              <Btn variant="ghost" onClick={() => setEditing(null)}>Cancel</Btn>
              <Btn onClick={saveEdit}>Save Changes</Btn>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

// ── CREATE GROUP tab ──────────────────────────────────────────────────────────

function CreateGroupTab({ showToast, onGroupsChanged }) {
  const blank = { name: "", description: "", permissions: Object.fromEntries(PERM_KEYS.map(p => [p.key, false])) };
  const [form, setForm]   = useState(blank);
  const [saving, setSaving] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      await axios.post("/api/groups", form);
      showToast(`Group "${form.name}" created.`, "success");
      const { data } = await axios.get("/api/groups");
      onGroupsChanged(data);
      setForm(blank);
    } catch (err) {
      showToast(err?.response?.data?.detail || "Create failed.", "error");
    } finally { setSaving(false); }
  };

  const setPermission = (key, val) =>
    setForm(f => ({ ...f, permissions: { ...f.permissions, [key]: val } }));

  return (
    <div className="max-w-2xl">
      <SectionTitle>Create New Group</SectionTitle>
      <form onSubmit={submit} className="space-y-5">
        <Input label="Group Name *" value={form.name} required
               onChange={(e) => setForm(f => ({ ...f, name: e.target.value }))} />
        <Input label="Description" value={form.description}
               onChange={(e) => setForm(f => ({ ...f, description: e.target.value }))} />
        <div>
          <label className="block text-xs font-medium text-slate-400 mb-2">Permissions</label>
          <PermGrid perms={form.permissions} onChange={setPermission} />
        </div>
        <Btn type="submit" disabled={saving}>
          {saving ? "Creating…" : "Create Group"}
        </Btn>
      </form>
    </div>
  );
}

// ── Modal helper ──────────────────────────────────────────────────────────────

function Modal({ title, onClose, children, wide = false }) {
  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className={`bg-slate-800 border border-slate-700 rounded-2xl shadow-2xl
                       w-full ${wide ? "max-w-2xl" : "max-w-md"} max-h-[90vh] overflow-y-auto`}>
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-700">
          <h3 className="font-semibold text-white">{title}</h3>
          <button onClick={onClose} className="text-slate-400 hover:text-white text-xl leading-none">✕</button>
        </div>
        <div className="px-6 py-5">{children}</div>
      </div>
    </div>
  );
}

// ── Main exported component ───────────────────────────────────────────────────

const INNER_TABS = [
  { id: "users",        label: "Users",        perm: "users_view"  },
  { id: "create-user",  label: "Create User",  perm: "users_write" },
  { id: "groups",       label: "Groups",       perm: "users_view"  },
  { id: "create-group", label: "Create Group", perm: "users_write" },
];

export default function UserAdmin() {
  const { permissions } = useAuth();
  const [tab,    setTab]    = useState("users");
  const [groups, setGroups] = useState([]);
  const [toast,  setToast]  = useState({ msg: "", type: "" });

  const showToast = useCallback((msg, type = "success") => {
    setToast({ msg, type });
    setTimeout(() => setToast({ msg: "", type: "" }), 4000);
  }, []);

  const canWrite = !!permissions?.users_write;

  // Visible inner tabs
  const visibleTabs = INNER_TABS.filter(t => permissions?.[t.perm]);
  // If current tab becomes invisible, reset to first visible
  const activeTab = visibleTabs.find(t => t.id === tab)?.id ?? visibleTabs[0]?.id ?? "users";

  return (
    <div>
      <Toast msg={toast.msg} type={toast.type} onClose={() => setToast({ msg: "", type: "" })} />

      {/* Inner tab bar */}
      <div className="flex gap-1 mb-6 bg-slate-800 rounded-xl p-1 border border-slate-700 w-fit">
        {visibleTabs.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-2 text-sm font-medium rounded-lg transition
              ${activeTab === t.id
                ? "bg-blue-600 text-white"
                : "text-slate-400 hover:text-slate-200 hover:bg-slate-700"}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === "users"        && (
        <UsersTab canWrite={canWrite} groups={groups} showToast={showToast} />
      )}
      {activeTab === "create-user"  && (
        <CreateUserTab groups={groups} showToast={showToast} />
      )}
      {activeTab === "groups"       && (
        <GroupsTab canWrite={canWrite} showToast={showToast} onGroupsChanged={setGroups} />
      )}
      {activeTab === "create-group" && (
        <CreateGroupTab showToast={showToast} onGroupsChanged={setGroups} />
      )}
    </div>
  );
}
