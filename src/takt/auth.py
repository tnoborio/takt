"""APIキー認証"""

from fastapi import Header, HTTPException, Depends, Request

from .tenant import Tenant


async def get_current_tenant(request: Request, x_api_key: str = Header(...)) -> Tenant:
    tenant_manager = request.app.state.tenant_manager
    tenant = tenant_manager.get_by_api_key(x_api_key)
    if not tenant:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return tenant
