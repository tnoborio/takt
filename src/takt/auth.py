"""認証 — Cookie セッション + ロール制御"""

from fastapi import Depends, HTTPException, Request

from .db import PlatformDB, User


async def get_current_user(request: Request) -> User:
    """Cookie の takt_session トークンからユーザーを解決する。"""
    db: PlatformDB = request.app.state.platform_db
    token = request.cookies.get("takt_session")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    session = db.get_auth_session(token)
    if not session:
        raise HTTPException(status_code=401, detail="Session expired")

    user = db.get_user_by_id(session.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    return user


def require_role(*allowed_roles: str):
    """指定ロールのみアクセスを許可するDependency。"""
    async def dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return dep
