"""テナント限定ファイル操作ツール — エージェントが使うMCPツール"""

from pathlib import Path


def _ensure_within(base: Path, target: Path):
    """パストラバーサル防止"""
    resolved = target.resolve()
    if not str(resolved).startswith(str(base.resolve())):
        raise PermissionError(f"Access denied: {target}")
    return resolved


def read_file(tenant_data_dir: Path, path: str) -> str:
    target = _ensure_within(tenant_data_dir, tenant_data_dir / path)
    if not target.exists():
        raise FileNotFoundError(f"Not found: {path}")
    return target.read_text()


def write_file(tenant_data_dir: Path, path: str, content: str) -> str:
    target = _ensure_within(tenant_data_dir, tenant_data_dir / path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return f"Written: {path}"


def list_files(tenant_data_dir: Path, path: str = ".") -> list[str]:
    target = _ensure_within(tenant_data_dir, tenant_data_dir / path)
    if not target.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")
    return [str(p.relative_to(tenant_data_dir)) for p in target.rglob("*") if p.is_file()]
