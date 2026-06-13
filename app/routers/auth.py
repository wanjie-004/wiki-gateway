"""Auth 路由（注册/登录/me）"""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from datetime import timedelta

from .. import db
from ..auth import (
    create_access_token,
    get_current_user,
    require_admin,
)
from ..config import settings
from ..models import (
    RegisterRequest, LoginRequest,
    TokenResponse, UserInfo,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_to_info(user: dict) -> UserInfo:
    return UserInfo(
        id=user["id"],
        username=user["username"],
        full_name=user.get("full_name"),
        role=user["role"],
        created_at=user["created_at"],
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(req: RegisterRequest):
    """注册新用户（默认 role=user）"""
    if not settings.allow_open_registration:
        raise HTTPException(
            status_code=403,
            detail="当前已关闭公开注册",
        )

    existing = db.get_user_by_username(req.username)
    if existing:
        raise HTTPException(
            status_code=400,
            detail="用户名已存在",
        )

    user_id = db.create_user(
        username=req.username,
        password=req.password,
        full_name=req.full_name,
        role="user",
    )

    user = db.get_user_by_id(user_id)
    token = create_access_token({"sub": str(user_id), "username": req.username})
    db.update_last_login(user_id)

    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        user=_user_to_info(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(form: OAuth2PasswordRequestForm = Depends()):
    """登录拿 JWT（OAuth2 password flow 兼容）"""
    user = db.get_user_by_username(form.username)
    if not user or not db.verify_password(form.password, user["password_hash"]):
        raise HTTPException(
            status_code=401,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    db.update_last_login(user["id"])
    token = create_access_token({"sub": str(user["id"]), "username": user["username"]})
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        user=_user_to_info(user),
    )


# 同时支持 JSON body 登录（方便 curl / 脚本）
@router.post("/login-json", response_model=TokenResponse)
async def login_json(req: LoginRequest):
    """JSON 形式登录（方便非 OAuth2 客户端）"""
    user = db.get_user_by_username(req.username)
    if not user or not db.verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    db.update_last_login(user["id"])
    token = create_access_token({"sub": str(user["id"]), "username": user["username"]})
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expire_minutes * 60,
        user=_user_to_info(user),
    )


@router.get("/me", response_model=UserInfo)
async def get_me(user: dict = Depends(get_current_user)):
    """当前登录用户信息"""
    return _user_to_info(user)


@router.get("/users", response_model=list[UserInfo])
async def list_all_users(_: dict = Depends(require_admin)):
    """列出所有用户（admin 专用）"""
    return [_user_to_info(u) for u in db.list_users()]
