# HyperMonitor v4 — Multi-Hypervisor Server Monitoring Platform

A full-stack, Dockerised monitoring platform for **VMware ESXi**, **Ubuntu KVM**, and **Hyper-V** (and optionally **Proxmox VE**).  
Displays live CPU, RAM, and storage utilisation with a full VM inventory, snapshots, events, user management, email reports, and a complete audit log.

---

## Architecture

```
┌──────────────┐  HTTP :3000   ┌───────────────────────────────┐
│   Browser    │ ◄──────────── │  frontend (React + Tailwind)  │
│ (any machine)│               │        Nginx 1.27-alpine       │
└──────────────┘               └───────────┬───────────────────┘
                                            │  /api/* proxy → :8000
                                ┌───────────▼───────────────────┐
                                │  backend  (FastAPI / uvicorn) │
                                │                               │
                                │  ┌─ Strategy Pattern ───────┐ │
                                │  │  HypervisorInterface     │ │
                                │  │  ESXiAdapter (pyVmomi)   │ │
                                │  │  KVMAdapter  (SSH+virsh) │ │
                                │  │  HyperVAdapter (WinRM)   │ │
                                │  │  ProxmoxAdapter (opt-in) │ │
                                │  └──────────────────────────┘ │
                                │  ┌─ Background poller ──────┐ │
                                │  │ Every 60 s: live poll    │ │
                                │  │ Results → in-memory cache│ │
                                │  │ Stale-while-revalidate   │ │
                                │  └──────────────────────────┘ │
                                │  ┌─ APScheduler ────────────┐ │
                                │  │ Daily/weekly email jobs  │ │
                                │  └──────────────────────────┘ │
                                └───────────┬───────────────────┘
                                            │  asyncpg → :5432
                                ┌───────────▼───────────────────┐
                                │  db  (PostgreSQL 16)          │
                                │  server_config  vm_metadata   │
                                │  vm_snapshot    vm_event      │
                                │  email_config   email_schedule│
                                │  auth_user      auth_group    │
                                │  audit_log                    │
                                └───────────────────────────────┘
```

**Performance:** A background asyncio task polls every hypervisor every 60 seconds and stores results in an in-memory cache.  
API responses are served from cache with stale-while-revalidate — pages load in <100 ms regardless of hypervisor response time.

---

## Features

| Feature | Details |
|---|---|
| **Live Monitoring** | CPU %, RAM used/total, storage used/total, per-drive breakdown, VM count |
| **VM Inventory** | Power state, vCPUs, RAM, owner, purpose, creation date — all filterable |
| **Composable Filters** | Hypervisor → Server cascade dropdowns + power-state pills + smart global search |
| **Smart Search (Req 4)** | Auto-detects IP prefix / server slug / free-text; OR across name/owner/purpose/IP |
| **Snapshot Panel** | Expandable per-VM snapshot panel; live fetch from hypervisor adapter |
| **VM Events** | Normalized event schema with extensible JSONB metadata column |
| **User Management** | bcrypt-hashed passwords, JWT auth, groups with granular permissions |
| **Forced PW Change** | Admin can require a user to set a new password on next login |
| **Audit Log** | Immutable append-only log for all user actions; filterable, paginated, purgeable |
| **Email Reports** | HTML + CSV attachments (dashboard + per-server VM inventory) |
| **Scheduled Reports** | Daily or weekly via APScheduler, stored in DB, survives restarts |
| **CSV Export** | Client-side presentation CSV for selected/all servers + VM inventory |
| **Hypervisor Strategy** | Plug-and-play adapters; adding Proxmox needs only 2 file edits, 0 route changes |

---

## Project Structure

```
monitoring-app/
├── backend/
│   ├── main.py              # FastAPI — all routes, dispatcher, cache integration
│   ├── cache.py             # In-memory 60 s TTL cache + background poller + stale-while-revalidate
│   ├── query_builder.py     # VMQueryBuilder — Builder Pattern for composable filtering
│   ├── scheduler.py         # APScheduler wrapper — daily/weekly email jobs
│   ├── mailer.py            # CSV builders + HTML email body + SMTP delivery
│   ├── database.py          # SQLAlchemy async engine (Postgres + SQLite fallback)
│   ├── models.py            # ORM: ServerConfig, VMMetadata, VMSnapshot, VMEvent,
│   │                        #      EmailConfig, EmailSchedule, AuthUser, AuthGroup,
│   │                        #      UserGroup, AuditLog
│   ├── auth.py              # bcrypt hashing, JWT, permission helpers
│   ├── auth_routes.py       # /api/auth/* — login, me, logout, change-password
│   ├── users_routes.py      # /api/users/* + /api/groups/* — CRUD + admin password reset
│   ├── events_routes.py     # /api/events — paginated audit log + stats + purge
│   ├── crypto.py            # AES-Fernet credential encryption
│   ├── hypervisors/
│   │   ├── __init__.py      # REGISTRY + get_adapter() factory
│   │   ├── base.py          # HypervisorInterface abstract base class
│   │   ├── esxi.py          # ESXiAdapter — pyVmomi vSphere API
│   │   ├── kvm.py           # KVMAdapter  — SSH + virsh + /proc
│   │   ├── hyperv.py        # HyperVAdapter — WinRM + PowerShell
│   │   └── proxmox.py       # ProxmoxAdapter — REST API (opt-in; see below)
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── App.jsx          # Shell: nav bar, auth gate, tab routing
│   │   ├── Dashboard.jsx    # Live cards + VM table + filters + snapshots + search
│   │   ├── ManageServers.jsx# Server CRUD + VM Metadata editor
│   │   ├── EmailSettings.jsx# SMTP | Schedule | Send Now tabs
│   │   ├── UserAdmin.jsx    # Users + Groups management
│   │   ├── EventsLog.jsx    # Audit log with filters + purge
│   │   ├── AuthContext.jsx  # JWT state, axios interceptor, login/logout
│   │   ├── Login.jsx        # Full-page login form
│   │   ├── ForceChangePw.jsx# Mandatory password-change overlay
│   │   ├── index.js
│   │   └── index.css
│   ├── public/index.html
│   ├── nginx.conf
│   ├── package.json
│   └── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Quick Start

### Prerequisites
- Docker Desktop ≥ 24 (includes Compose v2)

### 1 · Build and start

```bash
cd monitoring-app
docker compose up --build -d
```

### 2 · Open the dashboard

| URL | What you get |
|---|---|
| `http://<HOST_IP>:3000` | Dashboard |
| `http://<HOST_IP>:8000/docs` | Swagger UI |

### 3 · Login

| Field | Default |
|---|---|
| Username | `root` |
| Password | `Indrayan@123` |

> **Change the root password immediately after first login via the user-menu → Change Password.**

### 4 · Add your first server

1. Click **Manage Servers → Add Server**
2. Enter: Display Name, Hypervisor Type, IP Address, Username, Password
3. Click **Add Server** — hardware specs (RAM/CPU/Disk) are auto-detected via the Probe button
4. The dashboard refreshes from cache within 60 seconds

---

## Architecture Patterns

### 1 · Strategy Pattern — Hypervisor Adapters

Every hypervisor implements [`HypervisorInterface`](backend/hypervisors/base.py) with three required methods:

```python
class HypervisorInterface(abc.ABC):
    @abstractmethod
    def get_server_status(self) -> dict: ...   # host metrics + embedded VM list
    @abstractmethod
    def get_all_vms(self) -> list[dict]: ...   # VM inventory
    @abstractmethod
    def get_vm_snapshots(self, vm_name: str) -> list[dict]: ...  # snapshots
```

The [`REGISTRY`](backend/hypervisors/__init__.py) maps hypervisor type strings to adapter classes.  
`main.py` calls `get_adapter(row).get_server_status()` — it never knows which protocol is used.

**Adding Proxmox VE** (or any new hypervisor) requires **exactly 2 changes**:
1. Uncomment `from .proxmox import ProxmoxAdapter` in `hypervisors/__init__.py`
2. Uncomment `"Proxmox VE": ProxmoxAdapter` in the `REGISTRY` dict
3. Uncomment `"Proxmox VE"` in `ManageServers.jsx`'s `HV_TYPES` array

Zero changes to routes, cache, or any business logic.

---

### 2 · Builder Pattern — Composable VM Filtering

[`VMQueryBuilder`](backend/query_builder.py) chains predicate lambdas — no nested if/else:

```python
results = (
    VMQueryBuilder(all_vms)
    .filter_hypervisor(hypervisor_type)  # None = no-op
    .filter_server(server_id)
    .filter_power_state(power_state)
    .filter_status(status)
    .search(search)                       # auto-detects IP / slug / free-text
    .build()
)
```

The frontend [`applyVMFilters()`](frontend/src/Dashboard.jsx) mirrors the same logic for instant zero-latency filtering.

---

### 3 · Unified Data Models

**Snapshots** (`vm_snapshot` table): `snap_name`, `created_at` (UTC ISO), `size_bytes` (normalized to bytes), `extra` (JSONB for hypervisor-specific fields).

**Events** (`vm_event` table): `event_type`, `severity`, `message`, `occurred_at`, `event_metadata` (JSONB) — extensible without migrations.

**Audit Log** (`audit_log` table): immutable append-only log of all user actions and system events.

---

### 4 · Smart Global Search

The search input auto-classifies the query:

| Input pattern | Match strategy |
|---|---|
| `192.168.1` (IP-like) | Substring match on `ip_address` |
| `prod-kvm-7f2a1b` (slug-like) | Exact match on `host_server_id` or `vm_id` |
| `alice` / `web server` (free text) | OR across `vm_name`, `owner_name`, `purpose`, `ip_address` |

All other active filters (hypervisor, server, power state) are applied via AND on top of the search result.

---

## Hypervisor Prerequisites

### Ubuntu KVM

```bash
# 1. Enable SSH
sudo apt update && sudo apt install -y openssh-server
sudo systemctl enable --now ssh

# 2. Create a dedicated monitoring user
sudo useradd -m -s /bin/bash hypermonitor
sudo passwd hypermonitor

# 3. Add to libvirt group (required for virsh)
sudo usermod -aG libvirt hypermonitor

# 4. Allow password authentication in SSH if needed
# Edit /etc/ssh/sshd_config: PasswordAuthentication yes
sudo systemctl restart ssh

# 5. Open firewall
sudo ufw allow 22/tcp && sudo ufw reload
```

---

### VMware ESXi / vCenter

**ESXi standalone:**
```
1. ESXi web UI → Manage → Security & Users → Users → Add user
   Username: hypermonitor | Password: <your password>
2. Host → Actions → Permissions → Add user
   User: hypermonitor | Role: Read-only
3. Ensure port 443 is open (default)
```

**vCenter:**
```
1. Administration → SSO → Users and Groups → Add User: hypermonitor
2. Administration → Access Control → Global Permissions → Add
   User: hypermonitor@vsphere.local | Role: Read-only | Propagate: ✓
```

---

### Hyper-V (Windows Server)

Run as Administrator in PowerShell on the Hyper-V host:

```powershell
Enable-PSRemoting -Force
Set-Item WSMan:\localhost\Service\Auth\Basic $true
Set-Item WSMan:\localhost\Service\AllowUnencrypted $true
Set-Service WinRM -StartupType Automatic
New-NetFirewallRule -DisplayName "WinRM HTTP" -Direction Inbound -Protocol TCP -LocalPort 5985 -Action Allow

# Optional: dedicated monitoring user
$pwd = ConvertTo-SecureString "YourPassword123!" -AsPlainText -Force
New-LocalUser "hypermonitor" -Password $pwd -FullName "HyperMonitor Service"
Add-LocalGroupMember -Group "Hyper-V Administrators" -Member "hypermonitor"
Add-LocalGroupMember -Group "Remote Management Users" -Member "hypermonitor"
```

---

### Proxmox VE (opt-in)

```bash
# 1. Create API user with auditor role
pveum user add hypermonitor@pve --password <password>
pveum aclmod / -user hypermonitor@pve -role PVEAuditor

# 2. Enable the adapter (two lines to uncomment — see Architecture section)
# 3. Install dependency in requirements.txt: proxmoxer>=2.0.1
```

---

## API Reference

### Authentication
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/auth/login` | Login (form-encoded); returns JWT |
| `GET` | `/api/auth/me` | Current user + permissions |
| `POST` | `/api/auth/logout` | Logout (client-side token removal) |
| `POST` | `/api/auth/change-password` | Self-service password change |

### Server Management
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/servers/config` | Add a hypervisor host |
| `GET` | `/api/servers/config` | List all configured hosts |
| `PUT` | `/api/servers/config/{id}` | Update a host |
| `DELETE` | `/api/servers/config/{id}` | Remove a host |
| `PATCH` | `/api/servers/config/{id}/toggle` | Toggle enabled/disabled |
| `POST` | `/api/servers/probe/{id}` | Auto-detect hardware specs |

### Live Monitoring (served from cache)
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/servers` | Host metrics with stale-while-revalidate |
| `GET` | `/api/vms` | VM inventory with composable filters |
| `GET` | `/api/vms/{vm_id}` | Single VM detail |
| `GET` | `/api/vms/{vm_id}/snapshots` | Live snapshot fetch + persist |
| `GET` | `/api/hypervisors` | Aggregated stats per hypervisor type |
| `POST` | `/api/cache/refresh` | Force immediate re-poll |

### VM Metadata
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/vms/metadata` | All static VM records |
| `PUT` | `/api/vms/metadata/{vm_id}` | Create or update VM metadata |
| `DELETE` | `/api/vms/metadata/{vm_id}` | Delete VM metadata |
| `POST` | `/api/vms/metadata/bulk-upsert` | Bulk create/update |

### Snapshots & Events
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/snapshots` | List persisted snapshots (filterable) |
| `POST` | `/api/snapshots/ingest` | Trigger on-demand snapshot ingest |
| `GET` | `/api/vm-events` | List VM events (filterable) |
| `POST` | `/api/vm-events` | Ingest a normalized event |

### Users & Groups
| Method | Path | Description |
|---|---|---|
| `GET/POST` | `/api/users` | List / create users |
| `GET/PUT/DELETE` | `/api/users/{id}` | Get / update / delete user |
| `POST` | `/api/users/{id}/reset-password` | Admin password reset |
| `GET/POST` | `/api/groups` | List / create groups |
| `PUT/DELETE` | `/api/groups/{id}` | Update / delete group |

### Events / Audit Log
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/events` | Paginated audit log with filters |
| `GET` | `/api/events/stats` | Summary counts by category/severity |
| `DELETE` | `/api/events` | Purge entries older than N days |

### Email Reports
| Method | Path | Description |
|---|---|---|
| `GET/PUT` | `/api/email/config` | Get/save SMTP settings |
| `POST` | `/api/email/test` | Send test email |
| `POST` | `/api/email/send-report` | Send full report now |
| `GET/PUT/DELETE` | `/api/email/schedule` | Manage schedule |

### CSV Downloads
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/reports/servers.csv` | Dashboard summary CSV |
| `GET` | `/api/reports/vms.csv` | Full VM inventory CSV |
| `GET` | `/api/reports/vms/{server_id}.csv` | Per-server VM CSV |

---

## Permission Model

Groups carry a JSON dict of boolean feature flags. A user's effective permissions = union (OR) across all their groups. Root users bypass all checks.

| Permission key | Grants |
|---|---|
| `dashboard_view` | View live dashboard and VM inventory |
| `dashboard_write` | Inline-edit VM owner/purpose |
| `servers_view` | View Manage Servers page |
| `servers_write` | Add / edit / delete / probe servers |
| `email_view` | View email settings |
| `email_write` | Save SMTP, send reports, manage schedule |
| `users_view` | View Users & Groups tab |
| `users_write` | Create / edit / delete users and groups |
| `events_view` | View Events / Audit Log tab |
| `events_write` | Purge old log entries |

**Built-in groups:**
- **Administrator** — all permissions
- **Leads** — dashboard_view/write + events_view
- **Team** — dashboard_view + events_view (read-only)

---

## Performance Notes

| Metric | Value |
|---|---|
| Dashboard page load | < 100 ms (served from cache) |
| Background poll interval | 60 s per server |
| Cache strategy | Stale-while-revalidate — never blank screen |
| Workers | 1 (in-memory cache is process-local; async handles concurrency) |

---

## Development (without Docker)

```bash
# Backend
cd monitoring-app/backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --workers 1   # SQLite fallback, no DB needed

# Frontend (separate terminal)
cd monitoring-app/frontend
npm install --legacy-peer-deps
SKIP_PREFLIGHT_CHECK=true npm start
```

---

## Rebuilding After Code Changes

```bash
# Rebuild and restart everything
docker compose up --build -d

# Rebuild a single service
docker compose build --no-cache backend
docker compose up -d --no-deps backend

# View live logs
docker compose logs -f backend
docker compose logs -f frontend

# Full reset (removes containers AND postgres volume)
docker compose down -v
docker compose up --build -d
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Dashboard shows "Unable to reach the API" | `docker compose logs backend` — DB may still be initialising |
| Server card shows ⚠ error | Verify IP, credentials, and required port |
| Cache age > 90 s (amber) | Click **Refresh Now** to force a live re-poll |
| KVM: "Connection refused" | Ensure `openssh-server` is running on port 22 |
| KVM: "Permission denied" | Add user to `libvirt` group and reconnect SSH |
| ESXi: SSL error | Expected — self-signed cert; backend disables cert verification |
| Hyper-V: "WinRM connection refused" | Run `Enable-PSRemoting -Force` + open port 5985 |
| Hyper-V: "AuthenticationError" | `Set-Item WSMan:\localhost\Service\Auth\Basic $true` |
| Login: "Incorrect username or password" | Default credentials: `root` / `Indrayan@123` |
| Password change fails | Old password is required; min 8 characters for new password |
| Email test fails | Check SMTP credentials; Gmail requires an App Password |
| Build fails: AJV error | `docker compose build --no-cache frontend` |
