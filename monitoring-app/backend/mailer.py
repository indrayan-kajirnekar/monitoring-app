"""
mailer.py — Report generation and SMTP delivery for HyperMonitor.

Two report artefacts are built and attached to every email:

1. dashboard_summary.csv
   One row per hypervisor host with live CPU %, RAM used/total, storage
   used/total, VM count, and status.

2. vm_inventory_<server_name>.csv  (one file per host)
   Full VM inventory for that host: VM name, IP, power state, CPU %,
   vCPUs, RAM used / total, owner, creation date, purpose.

The HTML email body contains the same dashboard summary as an inline table
so recipients get an at-a-glance view without opening any attachment.

SMTP modes
──────────
  "smtps"    — SSL/TLS from the start (port 465).
  "starttls" — Plain connect then STARTTLS upgrade (port 587).
  "plain"    — No encryption (port 25 internal/corporate relay). Optional auth.

The smtp_mode field takes precedence. The legacy use_tls bool is still
honoured: use_tls=True → "smtps", use_tls=False with no smtp_mode → "starttls".
"""

from __future__ import annotations

import csv
import io
import logging
import re
import smtplib
import ssl
from collections import defaultdict
from datetime import datetime, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Presentation-quality CSV builders  (UTF-8 BOM so Excel opens correctly)
# ──────────────────────────────────────────────────────────────────────────────

def _build_dashboard_csv(servers: List[Dict]) -> bytes:
    """
    Return a presentation-ready CSV for the dashboard summary.
    Includes a KPI summary block at the top, then the per-host data table.
    UTF-8 BOM so Microsoft Excel auto-detects encoding.
    """
    buf = io.StringIO()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    total_vms     = sum(s.get("vm_count", 0) for s in servers)
    critical_ct   = sum(1 for s in servers if s.get("status") == "critical")
    warning_ct    = sum(1 for s in servers if s.get("status") == "warning")
    avg_cpu       = (
        round(sum(s.get("cpu_usage_pct", 0) for s in servers) / len(servers), 1)
        if servers else 0
    )

    # ── KPI summary block ─────────────────────────────────────────────────────
    buf.write("HYPERMONITOR — INFRASTRUCTURE DASHBOARD REPORT\n")
    buf.write(f"Generated: {now}\n")
    buf.write("\n")
    buf.write("EXECUTIVE SUMMARY\n")
    buf.write(f"Total Hypervisor Hosts,{len(servers)}\n")
    buf.write(f"Total Virtual Machines,{total_vms}\n")
    buf.write(f"Average CPU Utilisation,{avg_cpu}%\n")
    buf.write(f"Hosts in Critical State,{critical_ct}\n")
    buf.write(f"Hosts in Warning State,{warning_ct}\n")
    buf.write(f"Hosts Online,{len(servers) - critical_ct - warning_ct}\n")
    buf.write("\n")

    # ── Per-host data table ───────────────────────────────────────────────────
    buf.write("HOST UTILISATION DETAIL\n")
    fields = [
        "Display Name", "IP Address", "Hypervisor Type",
        "CPU Usage %", "CPU Cores",
        "RAM Used (GB)", "RAM Total (GB)", "RAM Usage %",
        "Storage Used (TB)", "Storage Total (TB)", "Storage Usage %",
        "VM Count", "Status",
    ]
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for s in servers:
        writer.writerow({
            "Display Name":        s.get("display_name", ""),
            "IP Address":          s.get("ip_address", ""),
            "Hypervisor Type":     s.get("hypervisor_type", ""),
            "CPU Usage %":         s.get("cpu_usage_pct", 0),
            "CPU Cores":           s.get("cpu_cores", 0),
            "RAM Used (GB)":       s.get("ram_used_gb", 0),
            "RAM Total (GB)":      s.get("ram_total_gb", 0),
            "RAM Usage %":         s.get("ram_usage_pct", 0),
            "Storage Used (TB)":   s.get("storage_used_tb", 0),
            "Storage Total (TB)":  s.get("storage_total_tb", 0),
            "Storage Usage %":     s.get("storage_usage_pct", 0),
            "VM Count":            s.get("vm_count", 0),
            "Status":              s.get("status", "").upper(),
        })

    # UTF-8 BOM prefix ensures correct encoding detection in Excel
    return "\ufeff".encode("utf-8") + buf.getvalue().encode("utf-8")


def _build_vm_csv(vms: List[Dict], server_name: str) -> bytes:
    """
    Return a presentation-ready VM inventory CSV for a single host.
    Includes a summary block + full inventory table.
    UTF-8 BOM so Microsoft Excel auto-detects encoding.
    """
    buf = io.StringIO()

    now         = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    running_ct  = sum(1 for v in vms if v.get("power_state") == "running")
    stopped_ct  = sum(1 for v in vms if v.get("power_state") == "stopped")
    avg_cpu     = (
        round(sum(v.get("cpu_usage_pct", 0) for v in vms) / len(vms), 1)
        if vms else 0
    )

    # ── VM summary block ──────────────────────────────────────────────────────
    buf.write(f"HYPERMONITOR — VM INVENTORY REPORT: {server_name.upper()}\n")
    buf.write(f"Generated: {now}\n")
    buf.write("\n")
    buf.write("VM SUMMARY\n")
    buf.write(f"Total VMs,{len(vms)}\n")
    buf.write(f"Running,{running_ct}\n")
    buf.write(f"Stopped,{stopped_ct}\n")
    buf.write(f"Average CPU %,{avg_cpu}%\n")
    buf.write("\n")

    # ── VM inventory table ────────────────────────────────────────────────────
    buf.write("VM INVENTORY DETAIL\n")
    fields = [
        "VM Name", "IP Address", "Hypervisor Type", "Power State",
        "CPU Usage %", "vCPUs",
        "RAM Used (GB)", "RAM Total (GB)", "RAM Usage %",
        "Owner", "Creation Date", "Purpose", "Status",
    ]
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for v in vms:
        writer.writerow({
            "VM Name":         v.get("vm_name", ""),
            "IP Address":      v.get("ip_address", ""),
            "Hypervisor Type": v.get("hypervisor_type", ""),
            "Power State":     v.get("power_state", "").upper(),
            "CPU Usage %":     v.get("cpu_usage_pct", 0),
            "vCPUs":           v.get("cpu_cores", 0),
            "RAM Used (GB)":   v.get("ram_used_gb", 0),
            "RAM Total (GB)":  v.get("ram_total_gb", 0),
            "RAM Usage %":     v.get("ram_usage_pct", 0),
            "Owner":           v.get("owner_name", ""),
            "Creation Date":   v.get("creation_date", ""),
            "Purpose":         v.get("purpose", ""),
            "Status":          v.get("status", "").upper(),
        })

    return "\ufeff".encode("utf-8") + buf.getvalue().encode("utf-8")


# ──────────────────────────────────────────────────────────────────────────────
# HTML email body
# ──────────────────────────────────────────────────────────────────────────────

def _status_colour(status: str) -> str:
    return {"critical": "#dc2626", "warning": "#d97706", "online": "#16a34a"}.get(
        status.lower(), "#6b7280"
    )


def _build_html_body(servers: List[Dict], vms: List[Dict], generated_at: str) -> str:
    total_hosts = len(servers)
    total_vms   = len(vms)
    running_vms = sum(1 for v in vms if v.get("power_state") == "running")
    critical    = sum(1 for s in servers if s.get("status") == "critical")
    warning     = sum(1 for s in servers if s.get("status") == "warning")
    avg_cpu     = (
        round(sum(s.get("cpu_usage_pct", 0) for s in servers) / len(servers), 1)
        if servers else 0
    )

    rows_html = ""
    for s in servers:
        colour = _status_colour(s.get("status", ""))
        rows_html += f"""
        <tr>
          <td>{s.get('display_name','')}</td>
          <td style="font-family:monospace">{s.get('ip_address','')}</td>
          <td>{s.get('hypervisor_type','')}</td>
          <td>{s.get('cpu_usage_pct',0)}%</td>
          <td>{s.get('ram_used_gb',0)} / {s.get('ram_total_gb',0)} GB</td>
          <td>{s.get('storage_used_tb',0)} / {s.get('storage_total_tb',0)} TB</td>
          <td>{s.get('vm_count',0)}</td>
          <td><span style="color:{colour};font-weight:600">{s.get('status','').upper()}</span></td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"/>
<style>
  body {{ font-family: -apple-system, 'Segoe UI', sans-serif; font-size: 14px;
          color: #1f2328; margin: 0; padding: 0; background: #f7f8fa; }}
  .wrap {{ max-width: 760px; margin: 24px auto; background: #fff;
           border: 1px solid #e5e7eb; border-radius: 10px; overflow: hidden; }}
  .header {{ background: #0f172a; color: #fff; padding: 20px 28px; }}
  .header h1 {{ margin: 0; font-size: 18px; font-weight: 700; }}
  .header p  {{ margin: 4px 0 0; font-size: 12px; color: #94a3b8; }}
  .kpis {{ display: flex; gap: 0; border-bottom: 1px solid #e5e7eb; }}
  .kpi {{ flex: 1; padding: 16px 20px; border-right: 1px solid #e5e7eb; }}
  .kpi:last-child {{ border-right: none; }}
  .kpi-val {{ font-size: 26px; font-weight: 700; color: #1f2328; }}
  .kpi-lbl {{ font-size: 11px; color: #57606a; text-transform: uppercase;
               letter-spacing: .05em; }}
  .section {{ padding: 20px 28px; }}
  .section h2 {{ font-size: 14px; font-weight: 700; margin: 0 0 12px;
                  color: #1f2328; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ background: #f7f8fa; padding: 8px 10px; text-align: left;
        font-size: 11px; font-weight: 700; text-transform: uppercase;
        letter-spacing: .04em; color: #57606a;
        border-bottom: 1px solid #e5e7eb; }}
  td {{ padding: 9px 10px; border-bottom: 1px solid #f0f0f0; }}
  tr:last-child td {{ border-bottom: none; }}
  .footer {{ padding: 14px 28px; background: #f7f8fa;
             border-top: 1px solid #e5e7eb; font-size: 11px; color: #94a3b8; }}
  .alert-box {{ margin: 0 28px 16px; padding: 12px 16px; border-radius: 8px;
                background: #fef2f2; border: 1px solid #fecaca;
                color: #b91c1c; font-size: 13px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <h1>HyperMonitor — Infrastructure Report</h1>
    <p>Generated: {generated_at} &nbsp;·&nbsp; {total_hosts} host{'s' if total_hosts != 1 else ''} monitored</p>
  </div>

  <div class="kpis">
    <div class="kpi"><div class="kpi-val">{total_hosts}</div><div class="kpi-lbl">Total Hosts</div></div>
    <div class="kpi"><div class="kpi-val">{total_vms}</div><div class="kpi-lbl">Total VMs</div></div>
    <div class="kpi"><div class="kpi-val">{running_vms}</div><div class="kpi-lbl">Running VMs</div></div>
    <div class="kpi"><div class="kpi-val">{avg_cpu}%</div><div class="kpi-lbl">Avg CPU</div></div>
    <div class="kpi"><div class="kpi-val" style="color:{'#dc2626' if critical else '#16a34a'}">{critical}</div><div class="kpi-lbl">Critical</div></div>
    <div class="kpi"><div class="kpi-val" style="color:{'#d97706' if warning else '#16a34a'}">{warning}</div><div class="kpi-lbl">Warning</div></div>
  </div>

  {'<div class="alert-box">&#9888; ' + str(critical) + ' host(s) in CRITICAL state — immediate action required.</div>' if critical else ''}

  <div class="section">
    <h2>Dashboard Summary</h2>
    <table>
      <thead><tr>
        <th>Host</th><th>IP</th><th>Hypervisor</th>
        <th>CPU %</th><th>RAM</th><th>Storage</th><th>VMs</th><th>Status</th>
      </tr></thead>
      <tbody>{rows_html}</tbody>
    </table>
  </div>

  <div class="footer">
    Full VM inventory CSVs (one per host) are attached to this email.<br>
    This report was sent automatically by HyperMonitor.
  </div>
</div>
</body></html>"""


# ──────────────────────────────────────────────────────────────────────────────
# SMTP send
# ──────────────────────────────────────────────────────────────────────────────

def send_report(
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    use_tls: bool,
    from_address: str,
    recipients: List[str],
    servers: List[Dict],
    vms: List[Dict],
    smtp_mode: Optional[str] = None,   # "smtps" | "starttls" | "plain"
) -> None:
    """
    Build the full report and deliver it via SMTP.

    smtp_mode values:
      "smtps"    — SSL/TLS from the start (port 465)
      "starttls" — Plain connect + STARTTLS upgrade (port 587)
      "plain"    — No encryption; suitable for internal port-25 relays
    Raises on any connection or authentication failure.
    """
    if not recipients:
        raise ValueError("No recipients configured.")
    if not smtp_host:
        raise ValueError("SMTP host is not configured.")

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subject = f"HyperMonitor Report — {generated_at}"

    # ── Build email ──────────────────────────────────────────────────────────
    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"]    = from_address or smtp_user
    msg["To"]      = ", ".join(recipients)

    # HTML body
    html_body = _build_html_body(servers, vms, generated_at)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # Attachment 1: dashboard_summary.csv
    dash_csv = _build_dashboard_csv(servers)
    part = MIMEBase("application", "octet-stream")
    part.set_payload(dash_csv)
    encoders.encode_base64(part)
    part.add_header("Content-Disposition",
                    "attachment", filename="dashboard_summary.csv")
    msg.attach(part)

    # Attachment 2+: per-server VM inventory CSVs
    server_map = {s["server_id"]: s["display_name"] for s in servers}
    vms_by_server: Dict[str, List[Dict]] = defaultdict(list)
    for v in vms:
        vms_by_server[v.get("host_server_id", "unknown")].append(v)

    for sid, vm_list in vms_by_server.items():
        sname = re.sub(r"[^a-z0-9]+", "_",
                       server_map.get(sid, sid).lower()).strip("_")[:40]
        vm_csv = _build_vm_csv(vm_list, sname)
        part2 = MIMEBase("application", "octet-stream")
        part2.set_payload(vm_csv)
        encoders.encode_base64(part2)
        part2.add_header("Content-Disposition",
                         "attachment", filename=f"vms_{sname}.csv")
        msg.attach(part2)

    # ── Send ─────────────────────────────────────────────────────────────────
    #
    # smtp_mode determines the connection strategy:
    #   "smtps"    — SSL wraps the entire connection from the start (port 465)
    #   "starttls" — Plain TCP connect then STARTTLS upgrade (port 587)
    #   "plain"    — No encryption at all (port 25 internal/corporate relay)
    #
    # If smtp_mode is not supplied, fall back to the legacy use_tls bool:
    #   use_tls=True  → "smtps"
    #   use_tls=False → "starttls"
    # ─────────────────────────────────────────────────────────────────────────
    mode = smtp_mode or ("smtps" if use_tls else "starttls")
    context = ssl.create_default_context()

    if mode == "smtps":
        # SSL/TLS from the start (port 465)
        with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context,
                              timeout=30) as srv:
            if smtp_user and smtp_password:
                srv.login(smtp_user, smtp_password)
            srv.sendmail(msg["From"], recipients, msg.as_string())

    elif mode == "starttls":
        # Plain connect then STARTTLS upgrade (port 587)
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as srv:
            srv.ehlo()
            srv.starttls(context=context)
            srv.ehlo()
            if smtp_user and smtp_password:
                srv.login(smtp_user, smtp_password)
            srv.sendmail(msg["From"], recipients, msg.as_string())

    else:
        # Plain SMTP — no encryption (port 25 corporate/internal relay)
        # Common in data-centre environments where the relay is on the LAN.
        # Only authenticates if credentials are explicitly provided.
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as srv:
            srv.ehlo()
            if smtp_user and smtp_password:
                srv.login(smtp_user, smtp_password)
            srv.sendmail(msg["From"], recipients, msg.as_string())

    log.info("Report sent to %s via %s:%s (mode=%s)",
             recipients, smtp_host, smtp_port, mode)
