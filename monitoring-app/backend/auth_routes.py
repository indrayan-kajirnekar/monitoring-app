"""
auth_routes.py — FastAPI router for authentication + current-user endpoints.

Endpoints
─────────
POST /api/auth/login       – accept username+password, return JWT + user info
GET  /api/auth/me          – return current user + resolved permissions
POST /api/auth/logout      – client-side logout (token invalidation is client-side;
                             this endpoint is a no-op provided for completeness)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import SessionLocal
import models
import auth as _auth
from events_routes import log_event

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


# ── DB dependency ─────────────────────────────────────────────────────────────

async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    expires_in:   int           # seconds
    user:         "UserInfo"


class UserInfo(BaseModel):
    id:                   int
    username:             str
    full_name:            str
    email:                str
    is_root:              bool
    is_active:            bool
    must_change_password: bool
    group_ids:            List[int]
    group_names:          List[str]
    permissions:          Dict[str, bool]


class ChangePasswordPayload(BaseModel):
    current_password: str
    new_password:     str


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _build_user_info(user: models.AuthUser, db: AsyncSession) -> UserInfo:
    """Resolve groups + permissions and return a UserInfo dict."""
    perms = await _auth._resolve_permissions(user, db)

    gids = [int(x) for x in (user.group_ids_csv or "").split(",") if x.strip().isdigit()]
    group_names: List[str] = []
    if gids:
        result = await db.execute(
            select(models.AuthGroup).where(models.AuthGroup.id.in_(gids)))
        group_names = [g.name for g in result.scalars().all()]

    return UserInfo(
        id=user.id,
        username=user.username,
        full_name=user.full_name or "",
        email=user.email or "",
        is_root=user.is_root,
        is_active=user.is_active,
        must_change_password=bool(user.must_change_password),
        group_ids=gids,
        group_names=group_names,
        permissions=perms,
    )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db:   AsyncSession = Depends(get_db),
):
    """
    Authenticate with username + password.
    Returns a JWT access token valid for JWT_EXPIRE_HOURS (default 12 h).
    """
    actor_ip = request.client.host if request.client else ""
    result = await db.execute(
        select(models.AuthUser).where(models.AuthUser.username == form.username))
    user = result.scalars().first()

    if not user or not _auth.verify_password(form.password, user.password_hash):
        # Log failed login attempt
        await log_event(db, actor=form.username, category="auth",
                        action="login.fail", actor_ip=actor_ip,
                        detail=f"Failed login attempt for '{form.username}'",
                        severity="warning")
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
        )
    if not user.is_active:
        await log_event(db, actor=form.username, category="auth",
                        action="login.disabled", actor_ip=actor_ip,
                        detail=f"Login attempt on disabled account '{form.username}'",
                        severity="warning")
        await db.commit()
        raise HTTPException(status_code=403, detail="Account is disabled.")

    # Update last_login_at — store as naive UTC (column is TIMESTAMP WITHOUT TIME ZONE)
    user.last_login_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await log_event(db, actor=user.username, category="auth",
                    action="login.ok", actor_ip=actor_ip,
                    detail=f"Successful login from {actor_ip}")
    await db.commit()

    token = _auth.create_access_token(user.username, user.id)
    user_info = await _build_user_info(user, db)

    return TokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=_auth.JWT_EXPIRE_HOURS * 3600,
        user=user_info,
    )


@router.get("/me", response_model=UserInfo)
async def get_me(
    current_user: models.AuthUser = Depends(_auth.get_current_user),
    db:           AsyncSession    = Depends(get_db),
):
    """Return the current user's profile + resolved permissions."""
    return await _build_user_info(current_user, db)


@router.post("/logout")
async def logout(
    current_user: models.AuthUser = Depends(_auth.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Token invalidation is handled client-side (delete from localStorage).
    This endpoint exists so the frontend can make a clean POST /logout call.
    """
    await log_event(db, actor=current_user.username, category="auth",
                    action="logout", detail="User logged out")
    await db.commit()
    return {"status": "ok", "detail": "Logged out."}


@router.post("/change-password")
async def change_password(
    payload:      ChangePasswordPayload,
    current_user: models.AuthUser = Depends(_auth.get_current_user),
    db:           AsyncSession    = Depends(get_db),
):
    """
    Allow any authenticated user (including root) to change their own password.
    Verifies the current password, sets the new one, and clears must_change_password.
    """
    if not _auth.verify_password(payload.current_password, current_user.password_hash):
        await log_event(db, actor=current_user.username, category="auth",
                        action="password.change.fail",
                        detail="Incorrect current password provided", severity="warning")
        await db.commit()
        raise HTTPException(400, "Current password is incorrect.")
    if len(payload.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters.")

    new_hash = _auth.hash_password(payload.new_password)
    # Explicit UPDATE — bypasses ORM identity-map caching
    await db.execute(
        update(models.AuthUser)
        .where(models.AuthUser.id == current_user.id)
        .values(password_hash=new_hash, must_change_password=False)
    )
    await log_event(db, actor=current_user.username, category="auth",
                    action="password.change.ok",
                    detail="Password changed successfully")
    await db.commit()
    return {"status": "ok", "detail": "Password changed successfully."}
