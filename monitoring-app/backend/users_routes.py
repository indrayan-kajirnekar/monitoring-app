"""
users_routes.py — FastAPI router for user and group management.

Endpoints
─────────
Groups:
  GET    /api/groups              – list all groups
  POST   /api/groups              – create a custom group
  PUT    /api/groups/{id}         – update group name/description/permissions
  DELETE /api/groups/{id}         – delete (built-in groups are protected)

Users:
  GET    /api/users               – list all users
  POST   /api/users               – create a new user
  GET    /api/users/{id}          – get one user
  PUT    /api/users/{id}          – update user (name, email, groups, active)
  DELETE /api/users/{id}          – delete user (root cannot be deleted)
  POST   /api/users/{id}/reset-password – admin resets another user's password
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database import SessionLocal
import models
import auth as _auth
from events_routes import log_event

router = APIRouter(tags=["User Management"])


# ── DB dependency ─────────────────────────────────────────────────────────────

async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class GroupCreate(BaseModel):
    name:             str
    description:      str = ""
    permissions:      Dict[str, bool] = {}


class GroupUpdate(BaseModel):
    name:             Optional[str]  = None
    description:      Optional[str]  = None
    permissions:      Optional[Dict[str, bool]] = None


class GroupResponse(BaseModel):
    id:               int
    name:             str
    description:      str
    permissions:      Dict[str, bool]
    is_builtin:       bool
    member_count:     int = 0
    created_at:       str


class UserCreate(BaseModel):
    username:              str
    full_name:             str  = ""
    email:                 str  = ""
    password:              str
    group_ids:             List[int] = []
    is_active:             bool = True
    must_change_password:  bool = False   # force password change on first login

    @field_validator("password")
    @classmethod
    def pw_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters.")
        return v

    @field_validator("username")
    @classmethod
    def uname_clean(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Username is required.")
        if v == "root":
            raise ValueError("Username 'root' is reserved.")
        return v


class UserUpdate(BaseModel):
    full_name:        Optional[str]       = None
    email:            Optional[str]       = None
    group_ids:        Optional[List[int]] = None
    is_active:        Optional[bool]      = None


class AdminResetPassword(BaseModel):
    new_password:          str
    must_change_password:  bool = True   # default: force change on next login

    @field_validator("new_password")
    @classmethod
    def pw_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters.")
        return v


class UserResponse(BaseModel):
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
    created_at:           str
    last_login_at:        Optional[str]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_perms(json_str: str) -> Dict[str, bool]:
    try:
        raw = json.loads(json_str or "{}")
    except Exception:
        raw = {}
    return {k: bool(raw.get(k, False)) for k in models.ALL_PERM_KEYS}


def _fill_perms(partial: Dict[str, bool]) -> Dict[str, bool]:
    """Merge partial perm dict with False-defaults for any missing keys."""
    return {k: bool(partial.get(k, False)) for k in models.ALL_PERM_KEYS}


async def _group_member_counts(db: AsyncSession) -> Dict[int, int]:
    """Return {group_id: member_count} in a single query."""
    result = await db.execute(select(models.UserGroup))
    rows = result.all()
    counts: Dict[int, int] = {}
    for row in rows:
        counts[row[0].group_id] = counts.get(row[0].group_id, 0) + 1
    return counts


async def _build_user_response(user: models.AuthUser, db: AsyncSession) -> UserResponse:
    gids = [int(x) for x in (user.group_ids_csv or "").split(",") if x.strip().isdigit()]
    group_names: List[str] = []
    if gids:
        result = await db.execute(
            select(models.AuthGroup).where(models.AuthGroup.id.in_(gids)))
        group_names = [g.name for g in result.scalars().all()]

    perms = await _auth._resolve_permissions(user, db)

    return UserResponse(
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
        created_at=user.created_at.isoformat() if user.created_at else "",
        last_login_at=user.last_login_at.isoformat() if user.last_login_at else None,
    )


async def _sync_user_groups(user: models.AuthUser, group_ids: List[int],
                             db: AsyncSession) -> None:
    """Replace a user's group memberships with the supplied list."""
    # Remove all existing memberships
    await db.execute(
        delete(models.UserGroup).where(models.UserGroup.user_id == user.id))
    # Insert new ones
    for gid in group_ids:
        db.add(models.UserGroup(user_id=user.id, group_id=gid))
    # Update the denormalised CSV column
    user.group_ids_csv = ",".join(str(g) for g in group_ids)


# ── Group routes ──────────────────────────────────────────────────────────────

@router.get("/api/groups", response_model=List[GroupResponse],
            dependencies=[Depends(_auth.require_perm("users_view"))])
async def list_groups(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(models.AuthGroup).order_by(models.AuthGroup.id))
    groups = result.scalars().all()

    # Count members per group
    mc_result = await db.execute(select(models.UserGroup))
    counts: Dict[int, int] = {}
    for (ug,) in mc_result.fetchall():
        counts[ug.group_id] = counts.get(ug.group_id, 0) + 1

    return [
        GroupResponse(
            id=g.id,
            name=g.name,
            description=g.description or "",
            permissions=_parse_perms(g.permissions_json),
            is_builtin=g.is_builtin,
            member_count=counts.get(g.id, 0),
            created_at=g.created_at.isoformat() if g.created_at else "",
        )
        for g in groups
    ]


@router.post("/api/groups", response_model=GroupResponse, status_code=201,
             dependencies=[Depends(_auth.require_perm("users_write"))])
async def create_group(payload: GroupCreate, db: AsyncSession = Depends(get_db),
                       current_user: models.AuthUser = Depends(_auth.get_current_user)):
    dup = await db.execute(
        select(models.AuthGroup).where(models.AuthGroup.name == payload.name))
    if dup.scalars().first():
        raise HTTPException(400, f"Group name '{payload.name}' already exists.")

    row = models.AuthGroup(
        name=payload.name,
        description=payload.description,
        permissions_json=json.dumps(_fill_perms(payload.permissions)),
        is_builtin=False,
    )
    db.add(row)
    await db.flush()
    await log_event(db, actor=current_user.username, category="users",
                    action="group.create", target=payload.name,
                    detail=f"Created group '{payload.name}'")
    await db.commit()
    await db.refresh(row)
    return GroupResponse(
        id=row.id, name=row.name, description=row.description or "",
        permissions=_parse_perms(row.permissions_json),
        is_builtin=row.is_builtin, member_count=0,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


@router.put("/api/groups/{group_id}", response_model=GroupResponse,
            dependencies=[Depends(_auth.require_perm("users_write"))])
async def update_group(group_id: int, payload: GroupUpdate,
                       db: AsyncSession = Depends(get_db),
                       current_user: models.AuthUser = Depends(_auth.get_current_user)):
    result = await db.execute(
        select(models.AuthGroup).where(models.AuthGroup.id == group_id))
    row = result.scalars().first()
    if not row:
        raise HTTPException(404, f"Group {group_id} not found.")

    if payload.name is not None:
        # Check uniqueness (ignore self)
        dup = await db.execute(
            select(models.AuthGroup).where(
                models.AuthGroup.name == payload.name,
                models.AuthGroup.id   != group_id))
        if dup.scalars().first():
            raise HTTPException(400, f"Group name '{payload.name}' already in use.")
        row.name = payload.name
    if payload.description is not None:
        row.description = payload.description
    if payload.permissions is not None:
        row.permissions_json = json.dumps(_fill_perms(payload.permissions))

    await log_event(db, actor=current_user.username, category="users",
                    action="group.update", target=row.name,
                    detail=f"Updated group '{row.name}'")
    await db.commit()
    await db.refresh(row)

    mc = await db.execute(
        select(models.UserGroup).where(models.UserGroup.group_id == group_id))
    member_count = len(mc.fetchall())

    return GroupResponse(
        id=row.id, name=row.name, description=row.description or "",
        permissions=_parse_perms(row.permissions_json),
        is_builtin=row.is_builtin, member_count=member_count,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


@router.delete("/api/groups/{group_id}", status_code=204,
               dependencies=[Depends(_auth.require_perm("users_write"))])
async def delete_group(group_id: int, db: AsyncSession = Depends(get_db),
                       current_user: models.AuthUser = Depends(_auth.get_current_user)):
    result = await db.execute(
        select(models.AuthGroup).where(models.AuthGroup.id == group_id))
    row = result.scalars().first()
    if not row:
        raise HTTPException(404, f"Group {group_id} not found.")
    if row.is_builtin:
        raise HTTPException(400, "Built-in groups cannot be deleted. "
                            "You can edit their permissions.")
    name = row.name
    await db.delete(row)
    await log_event(db, actor=current_user.username, category="users",
                    action="group.delete", target=name,
                    detail=f"Deleted group '{name}'", severity="warning")
    await db.commit()


# ── User routes ───────────────────────────────────────────────────────────────

@router.get("/api/users", response_model=List[UserResponse],
            dependencies=[Depends(_auth.require_perm("users_view"))])
async def list_users(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(models.AuthUser).order_by(models.AuthUser.id))
    users = result.scalars().all()
    return [await _build_user_response(u, db) for u in users]


@router.post("/api/users", response_model=UserResponse, status_code=201,
             dependencies=[Depends(_auth.require_perm("users_write"))])
async def create_user(payload: UserCreate, db: AsyncSession = Depends(get_db),
                      current_user: models.AuthUser = Depends(_auth.get_current_user)):
    dup = await db.execute(
        select(models.AuthUser).where(models.AuthUser.username == payload.username))
    if dup.scalars().first():
        raise HTTPException(400, f"Username '{payload.username}' already exists.")

    user = models.AuthUser(
        username=payload.username,
        full_name=payload.full_name,
        email=payload.email,
        password_hash=_auth.hash_password(payload.password),
        is_root=False,
        is_active=payload.is_active,
        must_change_password=payload.must_change_password,
        group_ids_csv=",".join(str(g) for g in payload.group_ids),
    )
    db.add(user)
    await db.flush()

    for gid in payload.group_ids:
        db.add(models.UserGroup(user_id=user.id, group_id=gid))

    await log_event(db, actor=current_user.username, category="users",
                    action="user.create", target=payload.username,
                    detail=f"Created user '{payload.username}'" +
                           (" (must change password)" if payload.must_change_password else ""))
    await db.commit()
    await db.refresh(user)
    return await _build_user_response(user, db)


@router.get("/api/users/{user_id}", response_model=UserResponse,
            dependencies=[Depends(_auth.require_perm("users_view"))])
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(models.AuthUser).where(models.AuthUser.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(404, f"User {user_id} not found.")
    return await _build_user_response(user, db)


@router.put("/api/users/{user_id}", response_model=UserResponse,
            dependencies=[Depends(_auth.require_perm("users_write"))])
async def update_user(user_id: int, payload: UserUpdate,
                      db: AsyncSession = Depends(get_db),
                      current_user: models.AuthUser = Depends(_auth.get_current_user)):
    result = await db.execute(
        select(models.AuthUser).where(models.AuthUser.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(404, f"User {user_id} not found.")

    if user.is_root and payload.is_active is False:
        raise HTTPException(400, "Cannot disable the root account.")

    changes = []
    if payload.full_name  is not None:
        changes.append(f"name→{payload.full_name}")
        user.full_name  = payload.full_name
    if payload.email      is not None:
        changes.append(f"email→{payload.email}")
        user.email      = payload.email
    if payload.is_active  is not None:
        changes.append("enabled" if payload.is_active else "disabled")
        user.is_active  = payload.is_active
    if payload.group_ids  is not None:
        await _sync_user_groups(user, payload.group_ids, db)
        changes.append("groups updated")

    await log_event(db, actor=current_user.username, category="users",
                    action="user.update", target=user.username,
                    detail=f"Updated '{user.username}': {', '.join(changes) or 'no changes'}")
    await db.commit()
    await db.refresh(user)
    return await _build_user_response(user, db)


@router.delete("/api/users/{user_id}", status_code=204,
               dependencies=[Depends(_auth.require_perm("users_write"))])
async def delete_user(user_id: int, db: AsyncSession = Depends(get_db),
                      current_user: models.AuthUser = Depends(_auth.get_current_user)):
    result = await db.execute(
        select(models.AuthUser).where(models.AuthUser.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(404, f"User {user_id} not found.")
    if user.is_root:
        raise HTTPException(400, "The root account cannot be deleted.")
    uname = user.username
    await db.delete(user)
    await log_event(db, actor=current_user.username, category="users",
                    action="user.delete", target=uname,
                    detail=f"Deleted user '{uname}'", severity="warning")
    await db.commit()


@router.post("/api/users/{user_id}/reset-password",
             dependencies=[Depends(_auth.require_perm("users_write"))])
async def admin_reset_password(user_id: int, payload: AdminResetPassword,
                                db: AsyncSession = Depends(get_db),
                                current_user: models.AuthUser = Depends(_auth.get_current_user)):
    """
    Admin resets another user's password (no current-password required).
    Root user and Administrator group members can both use this endpoint.
    The caller must have users_write permission — this covers root (is_root=True)
    and any user in the Administrator group (which has users_write=True).
    """
    result = await db.execute(
        select(models.AuthUser).where(models.AuthUser.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(404, f"User {user_id} not found.")

    # Root account self-reset: allowed (root can always reset themselves).
    # Non-root resetting root: block — only root can reset root.
    if user.is_root and not current_user.is_root:
        raise HTTPException(403, "Only the root account can reset the root password.")

    new_hash = _auth.hash_password(payload.new_password)

    # Use an explicit UPDATE statement to bypass any ORM identity-map caching
    # and guarantee the new hash reaches the database in this transaction.
    await db.execute(
        update(models.AuthUser)
        .where(models.AuthUser.id == user_id)
        .values(
            password_hash=new_hash,
            must_change_password=payload.must_change_password,
        )
    )
    await log_event(db, actor=current_user.username, category="users",
                    action="user.reset_password", target=user.username,
                    detail=f"Admin reset password for '{user.username}'" +
                           (" (must change on login)" if payload.must_change_password else ""),
                    severity="warning")
    await db.commit()
    return {"status": "ok", "detail": f"Password reset for '{user.username}'."}
