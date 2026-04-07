"""FastAPI エントリポイント — Takt AI経営秘書プラットフォーム"""

import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .auth import get_current_user, require_role
from .db import PlatformDB, User, verify_password
from .tenant import Tenant, TenantManager
from .session import SessionStore
from .model_router import select_model
from . import tenant_tools

load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DATA_DIR = Path(os.environ.get("TAKT_DATA_DIR", "./data/tenants"))
PLATFORM_DB_PATH = Path(os.environ.get("TAKT_PLATFORM_DB", "./data/platform.db"))
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.platform_db = PlatformDB(PLATFORM_DB_PATH)
    app.state.tenant_manager = TenantManager(DATA_DIR)
    yield


app = FastAPI(title="Takt", version="0.1.0", lifespan=lifespan)


# --- Schemas ---

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    task_type: str = "default"


class ChatResponse(BaseModel):
    response: str
    session_id: str
    model: str


class LoginRequest(BaseModel):
    email: str
    password: str


class TenantCreateRequest(BaseModel):
    tenant_id: str
    name: str
    admin_email: str
    admin_password: str
    admin_name: str


class UserCreateRequest(BaseModel):
    tenant_id: str
    email: str
    password: str
    display_name: str
    role: str = "user"


class UserUpdateRequest(BaseModel):
    display_name: str | None = None
    role: str | None = None
    is_active: bool | None = None
    password: str | None = None


# --- Helper ---

def _get_tenant_for_user(request: Request, user: User) -> Tenant:
    tm: TenantManager = request.app.state.tenant_manager
    tenant = tm.get(user.tenant_id)
    if not tenant:
        raise HTTPException(status_code=500, detail="Tenant not found")
    return tenant


# --- Health ---

@app.get("/health")
async def health():
    return {"status": "ok", "service": "takt"}


# --- Auth routes ---

@app.post("/auth/login")
async def login(req: LoginRequest, request: Request):
    db: PlatformDB = request.app.state.platform_db
    result = db.get_user_by_email(req.email)
    if not result:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    user, pw_hash = result
    if not user.is_active:
        raise HTTPException(status_code=401, detail="Account is inactive")
    if not verify_password(req.password, pw_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = db.create_auth_session(user.id)

    redirect = "/admin" if user.role in ("platform_admin", "tenant_admin") else "/"
    response = JSONResponse({"user": asdict(user), "redirect": redirect})
    response.set_cookie(
        key="takt_session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=7 * 24 * 3600,
        path="/",
    )
    return response


@app.post("/auth/logout")
async def logout(request: Request):
    db: PlatformDB = request.app.state.platform_db
    token = request.cookies.get("takt_session")
    if token:
        db.delete_auth_session(token)
    response = JSONResponse({"ok": True})
    response.delete_cookie("takt_session", path="/")
    return response


@app.get("/auth/me")
async def me(user: User = Depends(get_current_user)):
    return asdict(user)


# --- Sessions API ---

@app.get("/api/sessions")
async def list_sessions(request: Request, user: User = Depends(get_current_user)):
    tenant = _get_tenant_for_user(request, user)
    store = SessionStore(tenant.sessions_db_path)
    return {"sessions": store.list_sessions(user.id)}


@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, request: Request, user: User = Depends(get_current_user)):
    tenant = _get_tenant_for_user(request, user)
    store = SessionStore(tenant.sessions_db_path)
    session = store.get_session(session_id)
    if not session or session.get("user_id") != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"messages": store.get_messages(session_id)}


# --- Chat ---

@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, request: Request, user: User = Depends(get_current_user)):
    """テナントのエージェントとチャット"""
    from claude_agent_sdk import (
        query, ClaudeAgentOptions, ResultMessage, AssistantMessage, TextBlock,
        SystemMessage,
    )

    tenant = _get_tenant_for_user(request, user)
    store = SessionStore(tenant.sessions_db_path)

    # セッション作成 or 継続
    is_new = req.session_id is None
    session_id = req.session_id or store.create_session(user_id=user.id)
    session = store.get_session(session_id)

    model = select_model(req.task_type)
    system_prompt = tenant.get_system_prompt()

    # MCP: Google連携
    mcp_servers = {}
    google_oauth_path = tenant.data_dir / "google_oauth.json"
    if google_oauth_path.exists():
        mcp_servers["google"] = {
            "command": "/usr/local/bin/fastmcp-gsuite",
            "cwd": str(tenant.data_dir),
            "env": {
                "GAUTH_FILE": ".gauth.json",
                "ACCOUNTS_FILE": ".accounts.json",
                "CREDENTIALS_DIR": ".",
            },
        }

    # SDK session_id で会話継続
    sdk_session_id = session.get("sdk_session_id") if session else None

    options = ClaudeAgentOptions(
        model=model,
        system_prompt=system_prompt,
        permission_mode="bypassPermissions",
        cwd=str(tenant.data_dir),
        max_turns=5,
        env={"ANTHROPIC_API_KEY": ANTHROPIC_API_KEY},
        mcp_servers=mcp_servers if mcp_servers else {},
    )
    # 既存セッションを継続
    if sdk_session_id:
        options.resume = sdk_session_id

    # ユーザーメッセージを保存
    store.add_message(session_id, "user", req.message)

    response_text = ""
    total_input_tokens = 0
    total_output_tokens = 0
    new_sdk_session_id = None

    async for message in query(prompt=req.message, options=options):
        if isinstance(message, SystemMessage) and hasattr(message, "session_id"):
            new_sdk_session_id = message.session_id
        elif isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    response_text = block.text
        elif isinstance(message, ResultMessage):
            if message.usage:
                total_input_tokens = message.usage.get("input_tokens", 0)
                total_output_tokens = message.usage.get("output_tokens", 0)
            if message.result:
                response_text = message.result
            if message.session_id:
                new_sdk_session_id = message.session_id

    # アシスタントメッセージを保存
    store.add_message(session_id, "assistant", response_text)

    # セッション更新（SDK session_id, title）
    update_kwargs = {}
    if new_sdk_session_id:
        update_kwargs["sdk_session_id"] = new_sdk_session_id
    if is_new and response_text:
        # 最初のメッセージからタイトルを生成（先頭30文字）
        title = req.message[:30] + ("..." if len(req.message) > 30 else "")
        update_kwargs["title"] = title
    if update_kwargs:
        store.update_session(session_id, **update_kwargs)

    store.record_usage(
        session_id=session_id,
        model=model,
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
    )

    return ChatResponse(
        response=response_text,
        session_id=session_id,
        model=model,
    )


# --- Admin API: Tenants ---

@app.get("/api/admin/tenants")
async def admin_list_tenants(request: Request, user: User = Depends(require_role("platform_admin"))):
    db: PlatformDB = request.app.state.platform_db
    tenants = db.list_tenants()
    return {"tenants": [asdict(t) for t in tenants]}


@app.post("/api/admin/tenants")
async def admin_create_tenant(req: TenantCreateRequest, request: Request, user: User = Depends(require_role("platform_admin"))):
    db: PlatformDB = request.app.state.platform_db
    tm: TenantManager = request.app.state.tenant_manager

    # DB にテナント作成
    db.create_tenant(req.tenant_id, req.name)
    # ファイルシステムにテナントディレクトリ作成
    tm.create_tenant(req.tenant_id, req.name, api_key=f"takt-{req.tenant_id}")
    # テナント管理者ユーザー作成
    admin_user = db.create_user(
        tenant_id=req.tenant_id,
        email=req.admin_email,
        password=req.admin_password,
        display_name=req.admin_name,
        role="tenant_admin",
    )

    return {"tenant_id": req.tenant_id, "admin_user": asdict(admin_user)}


@app.patch("/api/admin/tenants/{tenant_id}")
async def admin_update_tenant(tenant_id: str, body: dict, request: Request, user: User = Depends(require_role("platform_admin"))):
    db: PlatformDB = request.app.state.platform_db
    db.update_tenant(tenant_id, name=body.get("name"), is_active=body.get("is_active"))
    return {"ok": True}


# --- Admin API: Google OAuth ---

@app.get("/api/admin/tenants/{tenant_id}/google-oauth")
async def get_google_oauth(tenant_id: str, request: Request, user: User = Depends(require_role("platform_admin", "tenant_admin"))):
    if user.role == "tenant_admin" and user.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Cannot access other tenant's config")
    tm: TenantManager = request.app.state.tenant_manager
    tenant = tm.get(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    oauth_path = tenant.data_dir / "google_oauth.json"
    if oauth_path.exists():
        import json
        data = json.loads(oauth_path.read_text())
        return {"configured": True, "client_id_preview": data.get("client_id", "")[:20] + "..."}
    return {"configured": False}


@app.put("/api/admin/tenants/{tenant_id}/google-oauth")
async def save_google_oauth(tenant_id: str, body: dict, request: Request, user: User = Depends(require_role("platform_admin", "tenant_admin"))):
    if user.role == "tenant_admin" and user.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Cannot modify other tenant's config")
    tm: TenantManager = request.app.state.tenant_manager
    tenant = tm.get(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    import json

    client_id = body["client_id"]
    client_secret = body["client_secret"]

    # Takt内部の設定
    (tenant.data_dir / "google_oauth.json").write_text(json.dumps(
        {"client_id": client_id, "client_secret": client_secret}, indent=2))

    # fastmcp-gsuite 形式の .gauth.json (Google client_secrets format)
    gauth = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob"],
        }
    }
    (tenant.data_dir / ".gauth.json").write_text(json.dumps(gauth, indent=2))

    return {"ok": True}


# --- Google OAuth Authorization Flow ---

@app.post("/api/admin/tenants/{tenant_id}/google-authorize")
async def google_authorize_start(tenant_id: str, body: dict, request: Request, user: User = Depends(require_role("platform_admin", "tenant_admin"))):
    """OAuth認可URLを取得する。body: {"email": "user@example.com"}"""
    if user.role == "tenant_admin" and user.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Cannot access other tenant")
    tm: TenantManager = request.app.state.tenant_manager
    tenant = tm.get(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    gauth_path = tenant.data_dir / ".gauth.json"
    if not gauth_path.exists():
        raise HTTPException(status_code=400, detail="Google OAuth not configured. Save client_id/secret first.")

    from oauth2client.client import flow_from_clientsecrets
    scopes = [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://mail.google.com/",
        "https://www.googleapis.com/auth/calendar",
    ]
    flow = flow_from_clientsecrets(str(gauth_path), " ".join(scopes), redirect_uri="urn:ietf:wg:oauth:2.0:oob")
    flow.params["access_type"] = "offline"
    flow.params["approval_prompt"] = "force"
    auth_url = flow.step1_get_authorize_url()

    return {"auth_url": auth_url, "email": body.get("email", "")}


@app.post("/api/admin/tenants/{tenant_id}/google-callback")
async def google_authorize_callback(tenant_id: str, body: dict, request: Request, user: User = Depends(require_role("platform_admin", "tenant_admin"))):
    """認可コードを受け取ってトークンを保存。body: {"code": "...", "email": "user@example.com"}"""
    if user.role == "tenant_admin" and user.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Cannot access other tenant")
    tm: TenantManager = request.app.state.tenant_manager
    tenant = tm.get(tenant_id)
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    import json
    from oauth2client.client import flow_from_clientsecrets

    gauth_path = tenant.data_dir / ".gauth.json"
    email = body["email"]
    code = body["code"]

    scopes = [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://mail.google.com/",
        "https://www.googleapis.com/auth/calendar",
    ]
    flow = flow_from_clientsecrets(str(gauth_path), " ".join(scopes), redirect_uri="urn:ietf:wg:oauth:2.0:oob")
    credentials = flow.step2_exchange(code)

    # トークン保存
    token_path = tenant.data_dir / f".oauth2.{email}.json"
    token_path.write_text(credentials.to_json())

    # accounts.json 更新
    accounts_path = tenant.data_dir / ".accounts.json"
    accounts = {"accounts": []}
    if accounts_path.exists():
        accounts = json.loads(accounts_path.read_text())

    existing_emails = [a["email"] for a in accounts["accounts"]]
    if email not in existing_emails:
        accounts["accounts"].append({"email": email, "account_type": "personal", "extra_info": ""})
    accounts_path.write_text(json.dumps(accounts, indent=2))

    return {"ok": True, "email": email}


# --- Admin API: Users ---

@app.get("/api/admin/users")
async def admin_list_users(request: Request, user: User = Depends(require_role("platform_admin", "tenant_admin"))):
    db: PlatformDB = request.app.state.platform_db
    if user.role == "platform_admin":
        users = db.list_users()
    else:
        users = db.list_users(tenant_id=user.tenant_id)
    return {"users": [asdict(u) for u in users]}


@app.post("/api/admin/users")
async def admin_create_user(req: UserCreateRequest, request: Request, user: User = Depends(require_role("platform_admin", "tenant_admin"))):
    db: PlatformDB = request.app.state.platform_db

    # tenant_admin は自テナントのみ、platform_admin は作成不可
    if user.role == "tenant_admin":
        if req.tenant_id != user.tenant_id:
            raise HTTPException(status_code=403, detail="Cannot create users in other tenants")
        if req.role == "platform_admin":
            raise HTTPException(status_code=403, detail="Cannot create platform admins")

    new_user = db.create_user(
        tenant_id=req.tenant_id,
        email=req.email,
        password=req.password,
        display_name=req.display_name,
        role=req.role,
    )
    return asdict(new_user)


@app.patch("/api/admin/users/{user_id}")
async def admin_update_user(user_id: str, req: UserUpdateRequest, request: Request, user: User = Depends(require_role("platform_admin", "tenant_admin"))):
    db: PlatformDB = request.app.state.platform_db

    target = db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    # tenant_admin は自テナントのみ
    if user.role == "tenant_admin" and target.tenant_id != user.tenant_id:
        raise HTTPException(status_code=403, detail="Cannot modify users in other tenants")
    if user.role == "tenant_admin" and req.role == "platform_admin":
        raise HTTPException(status_code=403, detail="Cannot promote to platform admin")

    db.update_user(user_id, display_name=req.display_name, role=req.role, is_active=req.is_active, password=req.password)
    return {"ok": True}


# --- Files (authenticated) ---

@app.get("/files")
async def list_files(path: str = ".", request: Request = None, user: User = Depends(get_current_user)):
    tenant = _get_tenant_for_user(request, user)
    files = tenant_tools.list_files(tenant.data_dir, path)
    return {"files": files}


@app.get("/files/{path:path}")
async def read_file(path: str, request: Request, user: User = Depends(get_current_user)):
    tenant = _get_tenant_for_user(request, user)
    content = tenant_tools.read_file(tenant.data_dir, path)
    return {"path": path, "content": content}


@app.put("/files/{path:path}")
async def write_file(path: str, body: dict, request: Request, user: User = Depends(get_current_user)):
    tenant = _get_tenant_for_user(request, user)
    result = tenant_tools.write_file(tenant.data_dir, path, body["content"])
    return {"result": result}


# --- UI ---

@app.get("/login")
async def login_page(request: Request):
    # Already logged in → redirect
    token = request.cookies.get("takt_session")
    if token:
        db: PlatformDB = request.app.state.platform_db
        session = db.get_auth_session(token)
        if session:
            return RedirectResponse("/")
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/admin")
async def admin_page(user: User = Depends(require_role("platform_admin", "tenant_admin"))):
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/")
async def index(request: Request):
    token = request.cookies.get("takt_session")
    if not token:
        return RedirectResponse("/login")
    db: PlatformDB = request.app.state.platform_db
    session = db.get_auth_session(token)
    if not session:
        return RedirectResponse("/login")
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# --- エントリポイント ---

def main():
    import uvicorn

    host = os.environ.get("TAKT_HOST", "0.0.0.0")
    port = int(os.environ.get("TAKT_PORT", "8000"))
    uvicorn.run("takt.server:app", host=host, port=port, reload=True)


if __name__ == "__main__":
    main()
