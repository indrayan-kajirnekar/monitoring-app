"""
models.py — SQLAlchemy ORM models.

Tables
──────
• vm_metadata      – static VM attributes (owner, purpose, creation date)
• server_config    – user-added hypervisor hosts with encrypted credentials
• email_config     – SMTP settings + recipient list for scheduled reports
• email_schedule   – APScheduler job persistence
• auth_group       – named permission groups (Administrator, Leads, Team, …)
• auth_user        – application user accounts with hashed passwords
• user_group       – many-to-many join table (users ↔ groups)
• audit_log        – immutable event/activity log (all actions written here)

Permission model
────────────────
Groups carry a JSON-encoded dict of feature flags.  All flags are booleans.
A user's effective permissions = union of all their groups' permissions.
Root user always has every permission regardless of groups.

Available permission keys:
  dashboard_view       – see the main dashboard
  dashboard_write      – inline-edit VM owner/purpose
  servers_view         – see Manage Servers list
  servers_write        – add / edit / delete servers, probe
  email_view           – see Email Reports tab
  email_write          – save SMTP settings, send reports
  users_view           – see Users & Groups tab
  users_write          – create/edit/delete users and groups
  events_view          – see Events / Audit Log tab
  events_write         – (reserved for future: delete/export log entries)
"""

from datetime import date, datetime
from sqlalchemy import (BigInteger, Boolean, Column, Date, DateTime,
                        ForeignKey, Integer, String, Text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

# SQLite compat: fall back to Text when JSONB is not available
import sqlalchemy.dialects.sqlite as _sqlite
_JSONB = JSONB if hasattr(JSONB, "hashable") else Text   # runtime shim; proper one below
# Proper cross-dialect JSONB / Text column factory
def _jsonb_col(**kw):
    """Return JSONB on Postgres, Text on SQLite (stores JSON as a string)."""
    try:
        from sqlalchemy.dialects.postgresql import JSONB as _PG_JSONB
        return Column(_PG_JSONB, **kw)
    except Exception:
        return Column(Text, **kw)


class Base(DeclarativeBase):
    pass


class VMMetadata(Base):
    """Static attributes of each VM — set once, rarely changed."""

    __tablename__ = "vm_metadata"

    vm_id           = Column(String(64),  primary_key=True, index=True)
    vm_name         = Column(String(128), nullable=False)
    ip_address      = Column(String(45),  nullable=False)
    hypervisor_type = Column(String(32),  nullable=False, index=True)
    owner_name      = Column(String(128), nullable=False)
    creation_date   = Column(Date,        nullable=False, default=date.today)
    purpose         = Column(String(256), nullable=False, default="")

    def __repr__(self) -> str:
        return f"<VMMetadata vm_id={self.vm_id}>"


class ServerConfig(Base):
    """
    A hypervisor host added by the user through the UI.

    Credentials are stored AES-encrypted (Fernet symmetric key).
    The encryption key lives only in the SECRET_KEY environment variable —
    never in this table.

    Columns
    ───────
    server_id        – slug generated from display_name + random suffix
    display_name     – human label shown in the UI
    ip_address       – IPv4 / IPv6 / FQDN used to reach the host
    hostname         – optional FQDN (if different from ip_address)
    hypervisor_type  – "VMware ESXi" | "Ubuntu KVM" | "Hyper-V"
    username_enc     – AES-encrypted username (base64)
    password_enc     – AES-encrypted password (base64)
    ram_total_gb     – total physical RAM for % calculations
    storage_total_tb – total storage for % calculations
    enabled          – soft-disable without deleting
    """

    __tablename__ = "server_config"

    server_id        = Column(String(64),  primary_key=True, index=True)
    display_name     = Column(String(128), nullable=False)
    ip_address       = Column(String(256), nullable=False)   # also accepts FQDN
    hostname         = Column(String(256), nullable=True,  default="")
    hypervisor_type  = Column(String(32),  nullable=False, index=True)
    username_enc     = Column(Text,        nullable=True,  default="")
    password_enc     = Column(Text,        nullable=True,  default="")
    # 0 = not yet detected; filled by /api/servers/probe/{server_id}
    ram_total_gb     = Column(String(16),  nullable=False, default="0")
    storage_total_tb = Column(String(16),  nullable=False, default="0")
    cpu_cores        = Column(String(8),   nullable=False, default="0")
    probe_status     = Column(String(16),  nullable=False, default="pending")
    enabled          = Column(Boolean,     nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<ServerConfig id={self.server_id} ip={self.ip_address}>"


class EmailConfig(Base):
    """
    SMTP configuration and report recipients.
    Only one row is expected (id=1). The UI upserts it.

    Columns
    ───────
    smtp_host         – SMTP server hostname (e.g. smtp.gmail.com)
    smtp_port         – SMTP port (25 / 465 / 587)
    smtp_user         – login username
    smtp_password_enc – AES-encrypted SMTP password
    use_tls           – True → SMTPS (port 465); False → STARTTLS (port 587)
    from_address      – "From:" address in outgoing emails
    recipients        – comma-separated list of recipient email addresses
    """

    __tablename__ = "email_config"

    id                = Column(Integer,     primary_key=True, default=1)
    smtp_host         = Column(String(256), nullable=False, default="")
    smtp_port         = Column(Integer,     nullable=False, default=587)
    smtp_user         = Column(String(256), nullable=False, default="")
    smtp_password_enc = Column(Text,        nullable=True,  default="")
    use_tls           = Column(Boolean,     nullable=False, default=False)
    # smtp_mode: "smtps" | "starttls" | "plain"
    # "plain" = no encryption at all (port 25 internal relay — the most common
    #            corporate setup where a relay is on the same LAN).
    smtp_mode         = Column(String(16),  nullable=True,  default="starttls")
    from_address      = Column(String(256), nullable=False, default="")
    recipients        = Column(Text,        nullable=False, default="")   # CSV

    def __repr__(self) -> str:
        return f"<EmailConfig smtp={self.smtp_host}:{self.smtp_port}>"


class EmailSchedule(Base):
    """
    Scheduled report delivery configuration (one row, id=1).

    Columns
    ───────
    schedule_type – "daily" | "weekly" | "disabled"
    hour          – UTC hour (0-23)
    minute        – UTC minute (0-59)
    day_of_week   – 0=Mon … 6=Sun (only used when schedule_type="weekly")
    enabled       – master on/off switch
    last_sent_at  – timestamp of last successful delivery (informational)
    """

    __tablename__ = "email_schedule"

    id            = Column(Integer,  primary_key=True, default=1)
    schedule_type = Column(String(16), nullable=False, default="disabled")
    hour          = Column(Integer,  nullable=False, default=8)
    minute        = Column(Integer,  nullable=False, default=0)
    day_of_week   = Column(Integer,  nullable=False, default=0)  # Mon
    enabled       = Column(Boolean,  nullable=False, default=False)
    last_sent_at  = Column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<EmailSchedule type={self.schedule_type} {self.hour:02d}:{self.minute:02d} UTC>"


# ──────────────────────────────────────────────────────────────────────────────
# Auth models
# ──────────────────────────────────────────────────────────────────────────────

# Default permission sets for the three built-in groups
PERM_ADMINISTRATOR = {
    "dashboard_view":  True, "dashboard_write": True,
    "servers_view":    True, "servers_write":   True,
    "email_view":      True, "email_write":     True,
    "users_view":      True, "users_write":     True,
    "events_view":     True, "events_write":    True,
}
PERM_LEADS = {
    "dashboard_view":  True, "dashboard_write": True,
    "servers_view":    False, "servers_write":  False,
    "email_view":      False, "email_write":    False,
    "users_view":      False, "users_write":    False,
    "events_view":     True,  "events_write":   False,
}
PERM_TEAM = {
    "dashboard_view":  True,  "dashboard_write": False,
    "servers_view":    False, "servers_write":   False,
    "email_view":      False, "email_write":     False,
    "users_view":      False, "users_write":     False,
    "events_view":     True,  "events_write":    False,
}
# All possible permission keys (used to fill blanks when a group is missing a key)
ALL_PERM_KEYS = list(PERM_ADMINISTRATOR.keys())


class AuthGroup(Base):
    """
    A named permission group.

    permissions_json – JSON-encoded dict of feature flags, e.g.:
        {"dashboard_view": true, "dashboard_write": false, ...}
    is_builtin – True for the three factory groups; prevents accidental deletion.
    """
    __tablename__ = "auth_group"

    id               = Column(Integer,     primary_key=True, autoincrement=True)
    name             = Column(String(64),  nullable=False, unique=True, index=True)
    description      = Column(String(256), nullable=False, default="")
    permissions_json = Column(Text,        nullable=False, default="{}")
    is_builtin       = Column(Boolean,     nullable=False, default=False)
    created_at       = Column(DateTime,    nullable=False, default=lambda: datetime.utcnow().replace(tzinfo=None))

    def __repr__(self) -> str:
        return f"<AuthGroup name={self.name}>"


class AuthUser(Base):
    """
    Application user account.

    password_hash – bcrypt hash (never stored in plain text).
    is_root       – root flag; bypasses all permission checks.
                    Only one root user exists (username="root").
    is_active     – soft-disable account without deleting.
    group_ids     – comma-separated group IDs (denormalised for fast reads).
                    Kept in sync by the users endpoint.
    """
    __tablename__ = "auth_user"

    id            = Column(Integer,     primary_key=True, autoincrement=True)
    username      = Column(String(64),  nullable=False, unique=True, index=True)
    full_name     = Column(String(128), nullable=False, default="")
    email         = Column(String(256), nullable=True,  default="")
    password_hash = Column(Text,        nullable=False)
    is_root              = Column(Boolean,     nullable=False, default=False)
    is_active            = Column(Boolean,     nullable=False, default=True)
    # When True the user must change their password before using the app.
    # Automatically cleared by POST /api/auth/change-password.
    must_change_password = Column(Boolean,     nullable=False, default=False)
    # Comma-separated group IDs for fast permission resolution
    group_ids_csv        = Column(Text,        nullable=False, default="")
    created_at           = Column(DateTime,    nullable=False, default=lambda: datetime.utcnow().replace(tzinfo=None))
    last_login_at        = Column(DateTime,    nullable=True)

    def __repr__(self) -> str:
        return f"<AuthUser username={self.username} root={self.is_root}>"


class UserGroup(Base):
    """Many-to-many join between AuthUser and AuthGroup."""
    __tablename__ = "user_group"

    user_id  = Column(Integer, ForeignKey("auth_user.id",  ondelete="CASCADE"),
                       primary_key=True)
    group_id = Column(Integer, ForeignKey("auth_group.id", ondelete="CASCADE"),
                       primary_key=True)


class AuditLog(Base):
    """
    Immutable append-only event log.

    Every significant action in the application writes one row here.
    Rows are never updated or deleted by normal application code.

    Columns
    ───────
    id          – auto-increment primary key
    ts          – UTC timestamp of the event (naive, stored as TIMESTAMP)
    actor       – username who performed the action (or "system")
    actor_ip    – remote IP of the request (best-effort, may be empty)
    category    – coarse category: "auth" | "servers" | "vms" | "email"
                                    | "users" | "system"
    action      – short machine-readable verb, e.g. "login", "server.add"
    target      – optional: affected resource identifier (server_id, username…)
    detail      – optional: human-readable description / diff summary
    severity    – "info" | "warning" | "error"
    """
    __tablename__ = "audit_log"

    id       = Column(Integer,     primary_key=True, autoincrement=True, index=True)
    ts       = Column(DateTime,    nullable=False,
                      default=lambda: datetime.utcnow().replace(tzinfo=None),
                      index=True)
    actor    = Column(String(64),  nullable=False, default="system", index=True)
    actor_ip = Column(String(64),  nullable=False, default="")
    category = Column(String(32),  nullable=False, default="system", index=True)
    action   = Column(String(64),  nullable=False, default="", index=True)
    target   = Column(String(128), nullable=True,  default="")
    detail   = Column(Text,        nullable=True,  default="")
    severity = Column(String(16),  nullable=False, default="info")

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} actor={self.actor} action={self.action}>"


# ──────────────────────────────────────────────────────────────────────────────
# Req 3 — Unified Snapshot schema
# ──────────────────────────────────────────────────────────────────────────────

class VMSnapshot(Base):
    """
    Normalized snapshot record.

    Hypervisors expose snapshot data in wildly different shapes:
    • ESXi  — pyVmomi VirtualMachineSnapshot object (datetime, no disk size)
    • KVM   — virsh snapshot-list (date string, no size)
    • Hyper-V — Get-VMSnapshot + Get-ChildItem disk files (datetime + size)

    This table stores the lowest-common-denominator fields plus a JSONB
    'extra' column for any hypervisor-specific attributes that don't map
    cleanly to the standard columns.  The application layer never needs to
    know which hypervisor produced a given snapshot.

    Fields
    ──────
    id           – auto PK
    server_id    – FK to server_config.server_id
    vm_id        – derived stable VM ID (same format as VMMetadata.vm_id)
    vm_name      – display name of the VM
    snap_name    – snapshot label / description
    created_at   – UTC timestamp (ISO-8601); None if hypervisor does not expose it
    size_bytes   – allocated disk space in bytes; 0 when not known
    hypervisor_type – "VMware ESXi" | "Ubuntu KVM" | "Hyper-V" | …
    extra        – JSONB dict of any extra hypervisor-specific fields
                   (e.g. {"description": "…", "quiesced": true})
    fetched_at   – when this record was written/refreshed
    """
    __tablename__ = "vm_snapshot"

    id              = Column(Integer,     primary_key=True, autoincrement=True, index=True)
    server_id       = Column(String(64),  ForeignKey("server_config.server_id",
                                                     ondelete="CASCADE"),
                             nullable=False, index=True)
    vm_id           = Column(String(128), nullable=False, index=True)
    vm_name         = Column(String(128), nullable=False, index=True)
    snap_name       = Column(String(256), nullable=False, default="")
    created_at      = Column(DateTime,    nullable=True)    # UTC; None = unknown
    size_bytes      = Column(BigInteger,  nullable=False, default=0)
    hypervisor_type = Column(String(32),  nullable=False, index=True)
    extra           = Column(Text,        nullable=True,  default="{}")  # JSON string; JSONB on PG
    fetched_at      = Column(DateTime,    nullable=False,
                             default=lambda: datetime.utcnow().replace(tzinfo=None))

    def __repr__(self) -> str:
        return f"<VMSnapshot vm={self.vm_name} snap={self.snap_name}>"


# ──────────────────────────────────────────────────────────────────────────────
# Req 3 — Extensible VM / Hypervisor Event schema
# ──────────────────────────────────────────────────────────────────────────────

class VMEvent(Base):
    """
    Normalized event record sourced from any hypervisor.

    Each hypervisor emits events with different field sets:
    • ESXi    – EventManager events (rich; task / alarm / user action)
    • KVM     – libvirt domain events (minimal; start/stop/define)
    • Hyper-V – Hyper-V WMI event log (varies by Windows version)

    Standard columns cover the shared minimal set.  The 'event_metadata' column
    (JSONB on Postgres, TEXT/JSON on SQLite) stores all remaining fields
    without a rigid schema so new hypervisors never require a migration.

    NOTE: the column is named 'event_metadata' (not 'metadata') because
    SQLAlchemy's DeclarativeBase reserves 'metadata' as a class-level attribute.

    Fields
    ──────
    id              – auto PK
    server_id       – FK to server_config.server_id
    vm_id           – derived stable VM ID (may be "" for host-level events)
    vm_name         – display name of the VM (or "" for host-level events)
    event_type      – normalized verb: "start" | "stop" | "snapshot.create" |
                      "snapshot.delete" | "migrate" | "error" | "user.login" | …
    severity        – "info" | "warning" | "error"
    message         – human-readable summary
    occurred_at     – UTC timestamp when the event happened on the hypervisor
    hypervisor_type – "VMware ESXi" | "Ubuntu KVM" | "Hyper-V" | …
    event_metadata  – JSONB / JSON dict for all extra hypervisor-specific
                      fields that don't fit the standard columns.
                      e.g. {"task_id": "task-123", "user": "admin",
                             "object_type": "VirtualMachine"}
    ingested_at     – when this row was written to the DB
    """
    __tablename__ = "vm_event"

    id              = Column(Integer,     primary_key=True, autoincrement=True, index=True)
    server_id       = Column(String(64),  ForeignKey("server_config.server_id",
                                                     ondelete="CASCADE"),
                             nullable=False, index=True)
    vm_id           = Column(String(128), nullable=False, default="", index=True)
    vm_name         = Column(String(128), nullable=False, default="", index=True)
    event_type      = Column(String(64),  nullable=False, default="", index=True)
    severity        = Column(String(16),  nullable=False, default="info")
    message         = Column(Text,        nullable=False, default="")
    occurred_at     = Column(DateTime,    nullable=True, index=True)
    hypervisor_type = Column(String(32),  nullable=False, index=True)
    event_metadata  = Column(Text,        nullable=True,  default="{}")  # JSON; JSONB on PG
    ingested_at     = Column(DateTime,    nullable=False,
                             default=lambda: datetime.utcnow().replace(tzinfo=None))

    def __repr__(self) -> str:
        return (
            f"<VMEvent id={self.id} type={self.event_type} vm={self.vm_name}>"
        )
