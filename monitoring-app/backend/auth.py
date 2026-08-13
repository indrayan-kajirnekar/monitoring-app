"""
auth.py — Authentication + authorisation helpers for HyperMonitor.

Responsibilities
────────────────
1. Password hashing / verification (bcrypt — used directly, no passlib)
2. JWT access-token creation + decoding (python-jose)
3. FastAPI dependency  get_current_user()  — validates Bearer token
4. Permission check helpers  require_perm()  — raises 403 on failure
5. Seed function  ensure_defaults()  — creates root user + 3 built-in
   groups on first startup (idempotent).

JWT design
──────────
  • Tokens are stateless HS256 JWTs signed with JWT_SECRET env var.
  • Payload: { "sub": "<username>", "uid": <user_id>, "exp": <epoch> }
  • Default lifetime: 12 hours (configurable via JWT_EXPIRE_HOURS env var).
  • No refresh-token scheme — users re-login when the token expires.
  • Logout is handled client-side by deleting the token from localStorage.

Why bcrypt directly (not passlib)?
───────────────────────────────────
  passlib 1.7.4 (last release: 2020) uses bcrypt.__about__.__version__ for
  backend detection.  bcrypt 4.0+ removed __about__, causing a hard crash at
  startup.  Using the bcrypt library directly avoids this coupling entirely.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt as _bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import SessionLocal
import models

log = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────

JWT_SECRET       = os.getenv("JWT_SECRET", "hypermonitor-jwt-secret-change-me")
JWT_ALGORITHM    = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "12"))

ROOT_DEFAULT_PASSWORD = "Indrayan@123"

# ── Password hashing (bcrypt direct — no passlib) ──────────────────────────────

def hash_password(plain: str) -> str:
    """Hash a plain-text password with bcrypt (work factor 12)."""
    return _bcrypt.hashpw(plain.encode(), _bcrypt.gensalt(rounds=12)).decode()

def verify_password(plain: str, hashed: str) -> bool:
    """Return True if plain matches the stored bcrypt hash."""
    try:
        return _bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ── JWT helpers ────────────────────────────────────────────────────────────────

def create_access_token(username: str, user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(hours=JWT_EXPIRE_HOURS)
    payload = {"sub": username, "uid": user_id, "exp": expire}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> Dict[str, Any]:
    """Raise HTTPException 401 if invalid / expired."""
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ── OAuth2 scheme (Bearer token in Authorization header) ──────────────────────

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


# ── Permission helpers ─────────────────────────────────────────────────────────

def _perms_from_json(json_str: str) -> Dict[str, bool]:
    """Parse permissions_json from an AuthGroup row, filling missing keys."""
    try:
        raw = json.loads(json_str or "{}")
    except (json.JSONDecodeError, TypeError):
        raw = {}
    # Ensure every key from ALL_PERM_KEYS is present; default False
    return {k: bool(raw.get(k, False)) for k in models.ALL_PERM_KEYS}


async def _resolve_permissions(
    user: models.AuthUser,
    db: AsyncSession,
) -> Dict[str, bool]:
    """
    Compute the effective permission set for a user.

    Root users always receive all permissions = True.
    For regular users: union (OR) across all their groups.
    """
    if user.is_root:
        return {k: True for k in models.ALL_PERM_KEYS}

    # Load all groups the user belongs to
    gids = [int(x) for x in (user.group_ids_csv or "").split(",") if x.strip().isdigit()]
    if not gids:
        return {k: False for k in models.ALL_PERM_KEYS}

    result = await db.execute(
        select(models.AuthGroup).where(models.AuthGroup.id.in_(gids)))
    groups = result.scalars().all()

    effective: Dict[str, bool] = {k: False for k in models.ALL_PERM_KEYS}
    for g in groups:
        gperms = _perms_from_json(g.permissions_json)
        for k, v in gperms.items():
            effective[k] = effective.get(k, False) or v
    return effective


# ── FastAPI dependencies ───────────────────────────────────────────────────────

async def get_db_for_auth() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db_for_auth),
) -> models.AuthUser:
    """Validate JWT and return the AuthUser row.  Raises 401 if invalid."""
    payload = decode_access_token(token)
    username: str = payload.get("sub", "")
    user_id:  int = payload.get("uid", 0)

    result = await db.execute(
        select(models.AuthUser).where(
            models.AuthUser.id == user_id,
            models.AuthUser.username == username,
        )
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled.")
    return user


def require_perm(permission: str):
    """
    Returns a FastAPI dependency that verifies the current user has
    `permission` set to True.  Raises HTTP 403 otherwise.

    Usage:
        @app.get("/api/servers", dependencies=[Depends(require_perm("servers_view"))])
    """
    async def _check(
        token: str = Depends(oauth2_scheme),
        db:    AsyncSession = Depends(get_db_for_auth),
    ):
        user = await get_current_user(token, db)
        perms = await _resolve_permissions(user, db)
        if not perms.get(permission, False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: '{permission}' is required.",
            )
        return user
    return _check


# ── Seed function — called on startup ─────────────────────────────────────────

async def ensure_defaults() -> None:
    """
    Create the three built-in permission groups and the root user
    if they do not already exist.  Idempotent — safe to call on every start.
    """
    async with SessionLocal() as db:
        # ── Built-in groups ────────────────────────────────────────────────
        builtin_groups = [
            {
                "name":             "Administrator",
                "description":      "Full access to all features.",
                "permissions_json": json.dumps(models.PERM_ADMINISTRATOR),
            },
            {
                "name":             "Leads",
                "description":      "Read/Write access to Dashboard only.",
                "permissions_json": json.dumps(models.PERM_LEADS),
            },
            {
                "name":             "Team",
                "description":      "Read-only access to Dashboard.",
                "permissions_json": json.dumps(models.PERM_TEAM),
            },
        ]
        admin_group_id: Optional[int] = None
        for g in builtin_groups:
            result = await db.execute(
                select(models.AuthGroup).where(models.AuthGroup.name == g["name"]))
            existing = result.scalars().first()
            if not existing:
                row = models.AuthGroup(
                    name=g["name"],
                    description=g["description"],
                    permissions_json=g["permissions_json"],
                    is_builtin=True,
                )
                db.add(row)
                await db.flush()   # populate row.id
                log.info("Created built-in group: %s", g["name"])
                if g["name"] == "Administrator":
                    admin_group_id = row.id
            else:
                if g["name"] == "Administrator":
                    admin_group_id = existing.id

        await db.commit()

        # ── Root user ──────────────────────────────────────────────────────
        result = await db.execute(
            select(models.AuthUser).where(models.AuthUser.username == "root"))
        root = result.scalars().first()
        if not root:
            root = models.AuthUser(
                username="root",
                full_name="Root Administrator",
                email="",
                password_hash=hash_password(ROOT_DEFAULT_PASSWORD),
                is_root=True,
                is_active=True,
                group_ids_csv=str(admin_group_id) if admin_group_id else "",
            )
            db.add(root)
            await db.flush()

            # Also insert the join-table row
            if admin_group_id:
                db.add(models.UserGroup(user_id=root.id, group_id=admin_group_id))

            await db.commit()
            log.info("Root user created (username=root, password=<default>)")
        else:
            log.info("Root user already exists — skipping seed.")
