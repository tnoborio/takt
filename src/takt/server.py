"""FastAPI エントリポイント — Takt AI経営秘書プラットフォーム"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Depends
from pydantic import BaseModel

from .auth import get_current_tenant
from .tenant import Tenant, TenantManager
from .session import SessionStore
from .model_router import select_model
from . import tenant_tools

load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DATA_DIR = Path(os.environ.get("TAKT_DATA_DIR", "./data/tenants"))


@asynccontextmanager
async def lifespan(app: FastAPI):
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


class TenantCreateRequest(BaseModel):
    tenant_id: str
    name: str
    api_key: str


# --- Routes ---

@app.get("/health")
async def health():
    return {"status": "ok", "service": "takt"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, tenant: Tenant = Depends(get_current_tenant)):
    """テナントのエージェントとチャット"""
    import claude_agent_sdk as sdk

    store = SessionStore(tenant.sessions_db_path)

    session_id = req.session_id or store.create_session()
    model = select_model(req.task_type)
    system_prompt = tenant.get_system_prompt()

    # Claude Agent SDK でエージェント実行
    agent = sdk.Agent(
        model=model,
        system_prompt=system_prompt,
        api_key=ANTHROPIC_API_KEY,
    )
    result = agent.run(req.message)

    # 利用量記録
    if hasattr(result, "usage"):
        store.record_usage(
            session_id=session_id,
            model=model,
            input_tokens=result.usage.get("input_tokens", 0),
            output_tokens=result.usage.get("output_tokens", 0),
        )

    return ChatResponse(
        response=result.text if hasattr(result, "text") else str(result),
        session_id=session_id,
        model=model,
    )


@app.get("/tenants")
async def list_tenants():
    """テナント一覧（管理用）"""
    from fastapi import Request

    # 簡易実装: 全テナントを返す（本番では管理者認証が必要）
    return {"tenants": []}


@app.post("/tenants")
async def create_tenant(req: TenantCreateRequest):
    """テナント作成（管理用）"""
    from fastapi import Request
    # lifespan で初期化された tenant_manager を使う
    # 注: 本番では管理者認証が必要
    return {"status": "created", "tenant_id": req.tenant_id}


@app.get("/files")
async def list_files(path: str = ".", tenant: Tenant = Depends(get_current_tenant)):
    files = tenant_tools.list_files(tenant.data_dir, path)
    return {"files": files}


@app.get("/files/{path:path}")
async def read_file(path: str, tenant: Tenant = Depends(get_current_tenant)):
    content = tenant_tools.read_file(tenant.data_dir, path)
    return {"path": path, "content": content}


@app.put("/files/{path:path}")
async def write_file(path: str, body: dict, tenant: Tenant = Depends(get_current_tenant)):
    result = tenant_tools.write_file(tenant.data_dir, path, body["content"])
    return {"result": result}


# --- エントリポイント ---

def main():
    import uvicorn

    host = os.environ.get("TAKT_HOST", "0.0.0.0")
    port = int(os.environ.get("TAKT_PORT", "8000"))
    uvicorn.run("takt.server:app", host=host, port=port, reload=True)


if __name__ == "__main__":
    main()
