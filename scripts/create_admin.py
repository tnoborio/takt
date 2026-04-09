"""プラットフォーム管理者の初期作成スクリプト

Usage:
    python scripts/create_admin.py --email admin@sasara.io --password xxx --name "Admin"
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from takt.db import PlatformDB


def main():
    parser = argparse.ArgumentParser(description="Create platform admin user")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--name", default="Platform Admin")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", "postgresql://takt:takt-dev@localhost:5432/takt"))
    args = parser.parse_args()

    db = PlatformDB(args.database_url)

    # "platform" テナントがなければ作成
    if not db.get_tenant("platform"):
        db.create_tenant("platform", "Sasara Platform")
        print("Created tenant: platform")

    # 既存ユーザーチェック
    if db.get_user_by_email(args.email):
        print(f"User {args.email} already exists")
        return

    user = db.create_user(
        tenant_id="platform",
        email=args.email,
        password=args.password,
        display_name=args.name,
        role="platform_admin",
    )
    print(f"Created platform admin: {user.email} (id: {user.id})")


if __name__ == "__main__":
    main()
