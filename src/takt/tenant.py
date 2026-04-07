"""テナント管理 — cwdベースのテナント分離"""

import json
import os
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class Tenant:
    tenant_id: str
    name: str
    api_key: str
    data_dir: Path
    config: dict = field(default_factory=dict)

    @property
    def claude_md_path(self) -> Path:
        return self.data_dir / "CLAUDE.md"

    @property
    def config_path(self) -> Path:
        return self.data_dir / "config.json"

    @property
    def sessions_db_path(self) -> Path:
        return self.data_dir / "sessions.db"

    def get_system_prompt(self) -> str:
        if self.claude_md_path.exists():
            return self.claude_md_path.read_text()
        return ""


class TenantManager:
    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._tenants: dict[str, Tenant] = {}
        self._api_key_index: dict[str, str] = {}
        self._load_all()

    def _load_all(self):
        for entry in self.base_dir.iterdir():
            if entry.is_dir() and (entry / "config.json").exists():
                self._load_tenant(entry.name)

    def _load_tenant(self, tenant_id: str):
        data_dir = self.base_dir / tenant_id
        config_path = data_dir / "config.json"
        config = json.loads(config_path.read_text())
        tenant = Tenant(
            tenant_id=tenant_id,
            name=config.get("name", tenant_id),
            api_key=config["api_key"],
            data_dir=data_dir,
            config=config,
        )
        self._tenants[tenant_id] = tenant
        self._api_key_index[tenant.api_key] = tenant_id

    def get_by_api_key(self, api_key: str) -> Tenant | None:
        tid = self._api_key_index.get(api_key)
        return self._tenants.get(tid) if tid else None

    def get(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    def create_tenant(self, tenant_id: str, name: str, api_key: str) -> Tenant:
        data_dir = self.base_dir / tenant_id
        data_dir.mkdir(parents=True, exist_ok=True)
        for sub in ["memory", "tasks", "daily"]:
            (data_dir / sub).mkdir(exist_ok=True)

        config = {"name": name, "api_key": api_key}
        (data_dir / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2))

        # デフォルトCLAUDE.mdをコピー
        default_md = Path(__file__).parent.parent.parent / "templates" / "default_claude.md"
        if default_md.exists():
            (data_dir / "CLAUDE.md").write_text(default_md.read_text())

        self._load_tenant(tenant_id)
        return self._tenants[tenant_id]

    def list_tenants(self) -> list[Tenant]:
        return list(self._tenants.values())
