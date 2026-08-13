/**
 * EmailSettings.jsx — SMTP config, scheduled reports, and on-demand send.
 *
 * Three tabs:
 *  1. SMTP Settings  — host, port, credentials, recipients, test button
 *  2. Schedule       — daily or weekly recurring delivery with next-run display
 *  3. Send Now       — immediate delivery + download CSV links
 */

import React, { useCallback, useEffect, useState } from "react";
import axios from "axios";

const API = process.env.REACT_APP_API_URL || "";

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const EMPTY_SMTP = {
  smtp_host: "", smtp_port: 587, smtp_user: "", smtp_password: "",
  use_tls: false, smtp_mode: "starttls", from_address: "", recipients: "",
};

// smtp_mode descriptions
const SMTP_MODES = [
  {
    id: "plain",
    label: "Plain / Relay",
    desc: "No encryption — port 25 internal relay (most common on corporate LAN)",
    port: 25,
  },
  {
    id: "starttls",
    label: "STARTTLS",
    desc: "Plain connect then TLS upgrade — port 587 (standard for cloud providers)",
    port: 587,
  },
  {
    id: "smtps",
    label: "SMTPS",
    desc: "TLS from the start — port 465 (Gmail SSL, some hosted servers)",
    port: 465,
  },
];

const PROVIDERS = [
  { name: "Plain relay",   host: "",                    port: 25,  mode: "plain"    },
  { name: "Gmail",         host: "smtp.gmail.com",      port: 587, mode: "starttls" },
  { name: "Gmail SSL",     host: "smtp.gmail.com",      port: 465, mode: "smtps"    },
  { name: "Outlook/365",   host: "smtp.office365.com",  port: 587, mode: "starttls" },
  { name: "Amazon SES",    host: "email-smtp.us-east-1.amazonaws.com", port: 587, mode: "starttls" },
  { name: "SendGrid",      host: "smtp.sendgrid.net",   port: 587, mode: "starttls" },
];

// ── Shared primitives ─────────────────────────────────────────────────────────

function Label({ children, hint }) {
  return (
    <label className="block text-xs font-semibold text-slate-600 mb-1">
      {children}
      {hint && <span className="ml-1 font-normal text-slate-400 normal-case">{hint}</span>}
    </label>
  );
}

function Input({ className = "", ...props }) {
  return (
    <input className={`w-full border border-slate-300 rounded-lg px-3 py-2 text-sm
      focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white ${className}`}
      {...props} />
  );
}

function Banner({ msg, type, onClose }) {
  if (!msg) return null;
  const s = { ok: "bg-emerald-50 border-emerald-200 text-emerald-800",
               error: "bg-red-50 border-red-200 text-red-700",
               info:  "bg-blue-50 border-blue-200 text-blue-800" };
  return (
    <div className={`flex items-start justify-between gap-3 border rounded-xl px-4 py-3 text-sm ${s[type]||s.info}`}>
      <span>{msg}</span>
      <button onClick={onClose} className="text-lg leading-none opacity-50 hover:opacity-100 shrink-0">×</button>
    </div>
  );
}

function Spinner() {
  return (
    <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z"/>
    </svg>
  );
}

// ── Tab 1: SMTP Settings ──────────────────────────────────────────────────────

function SmtpTab({ onBanner }) {
  const [form, setForm]       = useState({ ...EMPTY_SMTP });
  const [hasPwd, setHasPwd]   = useState(false);
  const [showPwd, setShowPwd] = useState(false);
  const [saving, setSaving]   = useState(false);
  const [testing, setTesting] = useState(false);
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  useEffect(() => {
    axios.get(`${API}/api/email/config`).then(r => {
      const d = r.data;
      setForm({
        smtp_host: d.smtp_host||"", smtp_port: d.smtp_port||587,
        smtp_user: d.smtp_user||"", smtp_password: "",
        use_tls:   d.use_tls||false,
        smtp_mode: d.smtp_mode || (d.use_tls ? "smtps" : "starttls"),
        from_address: d.from_address||"",
        recipients:   d.recipients||"",
      });
      setHasPwd(d.has_password||false);
    }).catch(() => {});
  }, []);

  const save = async (e) => {
    e.preventDefault();
    if (!form.smtp_host.trim()) { onBanner("SMTP Host is required.", "error"); return; }
    if (!form.recipients.trim()) { onBanner("At least one recipient is required.", "error"); return; }
    setSaving(true);
    try {
      await axios.put(`${API}/api/email/config`, form);
      onBanner("SMTP settings saved.", "ok");
      setHasPwd(true);
    } catch (e) {
      onBanner(e.response?.data?.detail || "Save failed.", "error");
    } finally { setSaving(false); }
  };

  const test = async () => {
    setTesting(true);
    try {
      const r = await axios.post(`${API}/api/email/test`);
      onBanner(`✓ ${r.data.message}`, "ok");
    } catch (e) {
      onBanner(e.response?.data?.detail || "Test failed.", "error");
    } finally { setTesting(false); }
  };

  return (
    <form onSubmit={save} className="flex flex-col gap-5">

      {/* Provider quick-fill */}
      <div className="bg-slate-50 border border-slate-200 rounded-xl p-4">
        <p className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-3">Quick-fill provider</p>
        <div className="grid grid-cols-3 sm:grid-cols-6 gap-2">
          {PROVIDERS.map(p => (
            <button key={p.name} type="button"
                    onClick={() => setForm(f => ({
                      ...f,
                      smtp_host: p.host || f.smtp_host,
                      smtp_port: p.port,
                      smtp_mode: p.mode,
                      use_tls:   p.mode === "smtps",
                    }))}
                    className="text-xs px-2 py-2 rounded-lg border border-slate-200 bg-white hover:border-blue-300 hover:bg-blue-50 text-center transition-colors">
              <span className="font-semibold text-slate-700 block">{p.name}</span>
              <span className="text-slate-400 font-mono text-[10px]">:{p.port}</span>
            </button>
          ))}
        </div>
      </div>

      {/* Host + Port */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="sm:col-span-2">
          <Label>SMTP Host *</Label>
          <Input placeholder="e.g. 8.8.8.8 or smtp.gmail.com" value={form.smtp_host}
                 onChange={e => set("smtp_host", e.target.value)} />
        </div>
        <div>
          <Label>Port *</Label>
          <Input type="number" value={form.smtp_port}
                 onChange={e => set("smtp_port", parseInt(e.target.value) || 25)} />
        </div>
      </div>

      {/* Connection mode — 3-way selector replacing the old TLS toggle */}
      <div>
        <Label hint="— choose how the client connects to your mail server">Connection Mode *</Label>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mt-1">
          {SMTP_MODES.map(m => (
            <button key={m.id} type="button"
                    onClick={() => setForm(f => ({
                      ...f,
                      smtp_mode: m.id,
                      use_tls:   m.id === "smtps",
                      smtp_port: f.smtp_port === 25 || f.smtp_port === 587 || f.smtp_port === 465
                        ? m.port : f.smtp_port,
                    }))}
                    className={`text-left px-4 py-3 rounded-xl border transition-colors
                      ${form.smtp_mode === m.id
                        ? "border-blue-500 bg-blue-50 ring-1 ring-blue-300"
                        : "border-slate-200 bg-white hover:border-slate-300"}`}>
              <p className={`text-sm font-semibold ${form.smtp_mode === m.id ? "text-blue-700" : "text-slate-700"}`}>
                {m.label}
                {form.smtp_mode === m.id && (
                  <span className="ml-2 text-[10px] bg-blue-500 text-white px-1.5 py-0.5 rounded-full font-normal">
                    selected
                  </span>
                )}
              </p>
              <p className="text-xs text-slate-400 mt-0.5 leading-snug">{m.desc}</p>
            </button>
          ))}
        </div>
        {form.smtp_mode === "plain" && (
          <p className="mt-2 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
            Plain mode sends email without encryption. Only use on a trusted internal network.
            Credentials are optional — leave blank if the relay accepts anonymous mail.
          </p>
        )}
      </div>

      {/* Credentials */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <Label>Username</Label>
          <Input placeholder="alerts@company.com" autoComplete="new-password"
                 value={form.smtp_user} onChange={e => set("smtp_user", e.target.value)} />
        </div>
        <div>
          <Label hint={hasPwd ? "— saved (blank = keep)" : ""}>Password</Label>
          <div className="relative">
            <Input type={showPwd ? "text" : "password"} placeholder={hasPwd ? "••••••••" : "Enter password"}
                   autoComplete="new-password" value={form.smtp_password}
                   onChange={e => set("smtp_password", e.target.value)} className="pr-12" />
            <button type="button" tabIndex={-1}
                    onClick={() => setShowPwd(p => !p)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-xs text-slate-400 hover:text-slate-600">
              {showPwd ? "Hide" : "Show"}
            </button>
          </div>
        </div>
      </div>

      <div>
        <Label hint="— shown as sender, falls back to username if blank">From Address</Label>
        <Input placeholder='HyperMonitor <alerts@company.com>'
               value={form.from_address} onChange={e => set("from_address", e.target.value)} />
      </div>

      <div>
        <Label>Recipients * <span className="font-normal text-slate-400 normal-case">(comma-separated)</span></Label>
        <textarea rows={3} value={form.recipients}
                  onChange={e => set("recipients", e.target.value)}
                  placeholder="manager@company.com, devops@company.com, sre@company.com"
                  className="w-full border border-slate-300 rounded-lg px-3 py-2 text-sm
                    focus:outline-none focus:ring-2 focus:ring-blue-400 bg-white resize-none" />
      </div>

      <div className="flex gap-3 flex-wrap">
        <button type="submit" disabled={saving}
                className="flex items-center gap-2 px-5 py-2 rounded-lg text-sm font-semibold bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50">
          {saving && <Spinner />}{saving ? "Saving…" : "Save Settings"}
        </button>
        <button type="button" disabled={testing} onClick={test}
                className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold border border-slate-300 text-slate-600 hover:bg-slate-50 disabled:opacity-50">
          {testing && <Spinner />}{testing ? "Testing…" : "Send Test Email"}
        </button>
      </div>
    </form>
  );
}

// ── Tab 2: Schedule ───────────────────────────────────────────────────────────

function ScheduleTab({ onBanner }) {
  const [form, setForm]     = useState({ schedule_type: "disabled", hour: 8, minute: 0, day_of_week: 0, enabled: true });
  const [nextRun, setNext]  = useState(null);
  const [lastSent, setLast] = useState(null);
  const [saving, setSaving] = useState(false);
  const [deleting, setDel]  = useState(false);
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const load = useCallback(() => {
    axios.get(`${API}/api/email/schedule`).then(r => {
      const d = r.data;
      setForm({ schedule_type: d.schedule_type||"disabled", hour: d.hour||8,
                minute: d.minute||0, day_of_week: d.day_of_week||0, enabled: d.enabled });
      setNext(d.next_run || null);
      setLast(d.last_sent_at || null);
    }).catch(() => {});
  }, []);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    setSaving(true);
    try {
      const r = await axios.put(`${API}/api/email/schedule`, form);
      setNext(r.data.next_run || null);
      onBanner(`Schedule saved. ${r.data.next_run ? `Next run: ${new Date(r.data.next_run).toLocaleString()}` : ""}`, "ok");
    } catch (e) {
      onBanner(e.response?.data?.detail || "Save failed.", "error");
    } finally { setSaving(false); }
  };

  const disable = async () => {
    setDel(true);
    try {
      await axios.delete(`${API}/api/email/schedule`);
      setNext(null);
      set("schedule_type", "disabled");
      set("enabled", false);
      onBanner("Schedule disabled.", "ok");
    } catch (e) {
      onBanner("Failed to disable.", "error");
    } finally { setDel(false); }
  };

  const pad = v => String(v).padStart(2, "0");

  return (
    <div className="flex flex-col gap-5">

      {/* Status chips */}
      <div className="flex gap-3 flex-wrap">
        <div className="bg-slate-50 border border-slate-200 rounded-xl px-4 py-3 flex flex-col gap-0.5 min-w-[180px]">
          <span className="text-xs text-slate-400 uppercase tracking-wider">Next run</span>
          <span className="text-sm font-semibold text-slate-700">
            {nextRun ? new Date(nextRun).toLocaleString() : <span className="text-slate-400">Not scheduled</span>}
          </span>
        </div>
        <div className="bg-slate-50 border border-slate-200 rounded-xl px-4 py-3 flex flex-col gap-0.5 min-w-[180px]">
          <span className="text-xs text-slate-400 uppercase tracking-wider">Last sent</span>
          <span className="text-sm font-semibold text-slate-700">
            {lastSent ? new Date(lastSent).toLocaleString() : <span className="text-slate-400">Never</span>}
          </span>
        </div>
      </div>

      {/* Schedule type */}
      <div>
        <Label>Frequency</Label>
        <div className="flex gap-3 flex-wrap">
          {["disabled", "daily", "weekly"].map(t => (
            <button key={t} type="button"
                    onClick={() => set("schedule_type", t)}
                    className={`px-4 py-2 rounded-lg text-sm font-medium border transition-colors capitalize
                      ${form.schedule_type === t
                        ? "border-blue-500 bg-blue-50 text-blue-700"
                        : "border-slate-200 text-slate-600 hover:border-slate-300"}`}>
              {t}
            </button>
          ))}
        </div>
      </div>

      {form.schedule_type !== "disabled" && (
        <>
          {/* Time */}
          <div className="grid grid-cols-2 gap-4 max-w-xs">
            <div>
              <Label>Hour (UTC, 0–23)</Label>
              <Input type="number" min={0} max={23} value={form.hour}
                     onChange={e => set("hour", Math.min(23, Math.max(0, parseInt(e.target.value)||0)))} />
            </div>
            <div>
              <Label>Minute (0–59)</Label>
              <Input type="number" min={0} max={59} value={form.minute}
                     onChange={e => set("minute", Math.min(59, Math.max(0, parseInt(e.target.value)||0)))} />
            </div>
          </div>
          <p className="text-xs text-slate-400 -mt-3">
            Time is in UTC. Preview: <strong className="text-slate-600">{pad(form.hour)}:{pad(form.minute)} UTC</strong>
          </p>

          {/* Day of week (weekly only) */}
          {form.schedule_type === "weekly" && (
            <div>
              <Label>Day of Week</Label>
              <div className="flex gap-2 flex-wrap">
                {DAYS.map((d, i) => (
                  <button key={d} type="button"
                          onClick={() => set("day_of_week", i)}
                          className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors
                            ${form.day_of_week === i
                              ? "border-blue-500 bg-blue-50 text-blue-700"
                              : "border-slate-200 text-slate-600 hover:border-slate-300"}`}>
                    {d.slice(0, 3)}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Preview sentence */}
          <div className="bg-blue-50 border border-blue-200 rounded-xl px-4 py-3 text-sm text-blue-800">
            {form.schedule_type === "daily"
              ? `Reports will be sent every day at ${pad(form.hour)}:${pad(form.minute)} UTC.`
              : `Reports will be sent every ${DAYS[form.day_of_week]} at ${pad(form.hour)}:${pad(form.minute)} UTC.`}
          </div>
        </>
      )}

      <div className="flex gap-3 flex-wrap">
        <button type="button" disabled={saving} onClick={save}
                className="flex items-center gap-2 px-5 py-2 rounded-lg text-sm font-semibold bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50">
          {saving && <Spinner />}{saving ? "Saving…" : "Save Schedule"}
        </button>
        {form.schedule_type !== "disabled" && (
          <button type="button" disabled={deleting} onClick={disable}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold border border-red-300 text-red-600 hover:bg-red-50 disabled:opacity-50">
            {deleting && <Spinner />}{deleting ? "Disabling…" : "Disable Schedule"}
          </button>
        )}
      </div>
    </div>
  );
}

// ── Tab 3: Send Now + CSV Downloads ──────────────────────────────────────────

// Format option definitions — single source of truth
const REPORT_FORMATS = [
  {
    value: "both",
    label: "HTML + CSV",
    desc:  "Inline HTML summary email with CSV attachments",
    icon:  "📬",
  },
  {
    value: "html",
    label: "HTML only",
    desc:  "Email contains only the inline HTML dashboard — no file attachments",
    icon:  "📄",
  },
  {
    value: "csv",
    label: "CSV only",
    desc:  "Plain-text email with CSV attachments — no HTML body",
    icon:  "📎",
  },
];

function SendNowTab({ onBanner }) {
  const [sending, setSending]   = useState(false);
  const [servers, setServers]   = useState([]);
  const [fmt, setFmt]           = useState("both");   // "html" | "csv" | "both"

  useEffect(() => {
    axios.get(`${API}/api/servers/config`).then(r => setServers(r.data)).catch(() => {});
  }, []);

  const sendReport = async () => {
    setSending(true);
    try {
      const r = await axios.post(`${API}/api/email/send-report`, { report_format: fmt });
      onBanner(`✓ ${r.data.message}`, "ok");
    } catch (e) {
      onBanner(e.response?.data?.detail || "Send failed.", "error");
    } finally { setSending(false); }
  };

  const dlLink = (path, label, icon) => (
    <a href={`${API}${path}`} download
       className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium border border-slate-300
         text-slate-700 hover:bg-slate-50 hover:border-slate-400 transition-colors">
      <span>{icon}</span>{label}
    </a>
  );

  return (
    <div className="flex flex-col gap-6">

      {/* Send report */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 flex flex-col gap-4">
        <div>
          <h3 className="font-bold text-slate-800">Send Full Report Now</h3>
          <p className="text-sm text-slate-500 mt-1">
            Collects live data from all enabled hosts and emails the report immediately to all configured recipients.
          </p>
        </div>

        {/* Format selector */}
        <div>
          <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Report Format</p>
          <div className="flex flex-col gap-2">
            {REPORT_FORMATS.map(f => (
              <label key={f.value}
                     className={`flex items-start gap-3 rounded-xl border px-4 py-3 cursor-pointer transition-colors
                       ${fmt === f.value
                         ? "border-slate-700 bg-slate-50"
                         : "border-slate-200 bg-white hover:border-slate-300"}`}>
                <input
                  type="radio"
                  name="report_format"
                  value={f.value}
                  checked={fmt === f.value}
                  onChange={() => setFmt(f.value)}
                  className="mt-0.5 accent-slate-700"
                />
                <span className="flex flex-col">
                  <span className="flex items-center gap-1.5 text-sm font-semibold text-slate-800">
                    <span>{f.icon}</span>{f.label}
                  </span>
                  <span className="text-xs text-slate-500 mt-0.5">{f.desc}</span>
                </span>
              </label>
            ))}
          </div>
        </div>

        <button disabled={sending} onClick={sendReport}
                className="self-start flex items-center gap-2 px-6 py-2.5 rounded-xl text-sm font-semibold bg-slate-800 text-white hover:bg-slate-700 disabled:opacity-50">
          {sending ? <><Spinner /> Collecting &amp; sending…</> : <>
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round"
                    d="M3 8l7.89 5.26a2 2 0 002.22 0L21 8M5 19h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
            </svg>
            Send Full Report Now
          </>}
        </button>
      </div>

      {/* Downloads */}
      <div className="bg-white border border-slate-200 rounded-xl p-5 flex flex-col gap-4">
        <div>
          <h3 className="font-bold text-slate-800">Download Reports</h3>
          <p className="text-sm text-slate-500 mt-1">
            Download current data. Data is served from the 60 s cache — no additional hypervisor queries.
          </p>
        </div>

        <div className="flex flex-col gap-3">
          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider">HTML Report</p>
          <div className="flex gap-3 flex-wrap">
            {dlLink("/api/reports/report.html", "Full Dashboard Report (.html)", "📄")}
          </div>

          <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mt-1">CSV Reports — All Servers</p>
          <div className="flex gap-3 flex-wrap">
            {dlLink("/api/reports/servers.csv", "Dashboard Summary (all hosts)", "📊")}
            {dlLink("/api/reports/vms.csv", "VM Inventory (all hosts)", "🖥")}
          </div>

          {servers.filter(s => s.enabled).length > 0 && (
            <>
              <p className="text-xs font-semibold text-slate-400 uppercase tracking-wider mt-1">CSV Reports — Per Server</p>
              <div className="flex gap-3 flex-wrap">
                {servers.filter(s => s.enabled).map(s => (
                  <a key={s.server_id}
                     href={`${API}/api/reports/vms/${s.server_id}.csv`}
                     download
                     className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium border border-slate-300
                       text-slate-700 hover:bg-slate-50 hover:border-slate-400 transition-colors">
                    <span>📎</span>
                    <span>{s.display_name}</span>
                    <span className="text-xs text-slate-400">VMs</span>
                  </a>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function EmailSettings() {
  const [tab, setTab]     = useState("smtp");
  const [banner, setBanner] = useState({ msg: "", type: "ok" });
  const onBanner = (msg, type = "ok") => setBanner({ msg, type });

  const TABS = [
    { id: "smtp",     label: "SMTP Settings" },
    { id: "schedule", label: "Schedule" },
    { id: "sendnow",  label: "Send Now & Download" },
  ];

  return (
    <div className="flex flex-col gap-5 max-w-2xl">
      <div>
        <h2 className="text-lg font-bold text-slate-800">Email Reports</h2>
        <p className="text-sm text-slate-500 mt-1">
          Configure SMTP, set up a recurring schedule, send reports on demand, or download CSVs for presentations.
        </p>
      </div>

      <Banner msg={banner.msg} type={banner.type} onClose={() => setBanner({ msg: "", type: "ok" })} />

      {/* Sub-tabs */}
      <div className="flex gap-1 border-b border-slate-200">
        {TABS.map(t => (
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

      {tab === "smtp"     && <SmtpTab     onBanner={onBanner} />}
      {tab === "schedule" && <ScheduleTab onBanner={onBanner} />}
      {tab === "sendnow"  && <SendNowTab  onBanner={onBanner} />}
    </div>
  );
}
