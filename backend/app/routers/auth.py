"""Authentication & user management routes."""

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    hash_password, verify_password, create_access_token,
    get_current_user, get_current_admin,
)
from app.config import settings
from app.database import get_db
from app.models import User, DEFAULT_USER_PERMISSIONS

router = APIRouter()


# ---------------------------------------------------------------------------
#  Schemas
# ---------------------------------------------------------------------------

class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserOut"


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    permissions: dict | None
    is_active: bool

    class Config:
        from_attributes = True


class UserCreate(BaseModel):
    username: str
    password: str
    display_name: str = ""
    role: str = "user"
    permissions: dict | None = None


class UserUpdate(BaseModel):
    display_name: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    permissions: Optional[dict] = None
    is_active: Optional[bool] = None


class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str


# ---------------------------------------------------------------------------
#  Login
# ---------------------------------------------------------------------------

@router.post("/login", response_model=TokenOut)
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == form.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect username or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    token = create_access_token(
        {"sub": user.username},
        expires_delta=timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
    )
    return TokenOut(access_token=token, user=UserOut.model_validate(user))


# ---------------------------------------------------------------------------
#  Current user
# ---------------------------------------------------------------------------

@router.get("/me", response_model=UserOut)
async def get_me(user: User = Depends(get_current_user)):
    return UserOut.model_validate(user)


@router.put("/me/password")
async def change_my_password(
    body: ChangePasswordIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(body.old_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Old password is incorrect")
    user.hashed_password = hash_password(body.new_password)
    await db.commit()
    return {"detail": "Password changed"}


# ---------------------------------------------------------------------------
#  Admin: user CRUD
# ---------------------------------------------------------------------------

@router.get("/users", response_model=list[UserOut])
async def list_users(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).order_by(User.id))
    return [UserOut.model_validate(u) for u in result.scalars().all()]


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    body: UserCreate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    # Check duplicate
    exists = await db.execute(select(func.count()).where(User.username == body.username))
    if exists.scalar() > 0:
        raise HTTPException(status_code=400, detail="Username already exists")

    user = User(
        username=body.username,
        hashed_password=hash_password(body.password),
        display_name=body.display_name or body.username,
        role=body.role if body.role in ("admin", "user") else "user",
        permissions=body.permissions if body.permissions else DEFAULT_USER_PERMISSIONS.copy(),
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)


@router.get("/users/{user_id}", response_model=UserOut)
async def get_user(
    user_id: int,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserOut.model_validate(user)


@router.put("/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    body: UserUpdate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if body.display_name is not None:
        user.display_name = body.display_name
    if body.password is not None:
        user.hashed_password = hash_password(body.password)
    if body.role is not None and body.role in ("admin", "user"):
        user.role = body.role
    if body.permissions is not None:
        user.permissions = body.permissions
    if body.is_active is not None:
        user.is_active = body.is_active

    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == "admin":
        # Prevent deleting the last admin
        admin_count = await db.execute(
            select(func.count()).where(User.role == "admin", User.is_active == True)
        )
        if admin_count.scalar() <= 1:
            raise HTTPException(status_code=400, detail="Cannot delete the last admin account")
    await db.delete(user)
    await db.commit()
    return {"detail": "User deleted"}


# ---------------------------------------------------------------------------
#  Permission reference
# ---------------------------------------------------------------------------

@router.get("/permissions")
async def list_permissions(user: User = Depends(get_current_user)):
    """Return the list of available permission keys with descriptions."""
    return {
        "keys": [
            {"key": "stocks", "label": "自选股管理"},
            {"key": "quotes", "label": "行情数据"},
            {"key": "strategy", "label": "策略交易"},
            {"key": "screener", "label": "智能选股"},
            {"key": "quant", "label": "量化分析"},
            {"key": "logs", "label": "抓取日志"},
            {"key": "schedule", "label": "定时更新"},
            {"key": "config", "label": "配置管理"},
        ]
    }
