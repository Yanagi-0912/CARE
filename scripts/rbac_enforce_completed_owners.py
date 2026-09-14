#!/usr/bin/env python3
"""把「已替每位家人指派角色」但仍在影子模式的擁有者切成 enforced。

擁有者指派完最後一位成員（或移除最後一位未設定者）時，服務層就會自動切換
（app/services/family/rbac_migration.py）。這支腳本只處理那段程式上線**之前**
就已經指派完的擁有者——他們不會再經過那條路徑。寫入用的是同一支函式，會用
最新的族譜再判一次，不會有兩套標準。

預設只列出會被切換的擁有者，加 --apply 才寫入。先跑
`scripts/rbac_migration_report.py` 確認判準 2（放寬差異）為 0：非零代表角色
解析或矩陣有錯，那時不該切任何人。

用法：
    python scripts/rbac_enforce_completed_owners.py
    python scripts/rbac_enforce_completed_owners.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def ready_owner_ids(docs: Iterable[dict[str, Any]]) -> list[str]:
    """初篩「有成員、每位成員都有角色、還沒切換」的擁有者。

    只是列清單用；真正寫入前 `enforce_if_assignment_complete` 會讀最新的族譜再判一次。
    """
    ready: list[str] = []
    for doc in docs:
        members = doc.get("family_members") or []
        if not members:
            continue
        if doc.get("rbac_migration_state") == "enforced":
            continue
        if any(not member.get("family_role") for member in members):
            continue
        ready.append(doc["user_id"])
    return ready


async def run(apply: bool) -> int:
    sys.path.insert(0, str(PROJECT_ROOT))
    from app.core.config import settings
    from app.db.mongodb import MongoDBManager
    from app.repositories.family_tree_repository import FamilyTreeRepository
    from app.services.family.rbac_migration import enforce_if_assignment_complete

    # App 啟動時由 main 的 lifespan 設定；腳本不經過那裡，要自己設。
    MongoDBManager.configure(settings.MONGODB_URI)
    collection = MongoDBManager.get_family_tree_collection()
    cursor = collection.find(
        {"family_members.0": {"$exists": True}},
        {"user_id": 1, "family_members": 1, "rbac_migration_state": 1},
    )
    owner_ids = ready_owner_ids(await cursor.to_list(length=None))

    if not owner_ids:
        print("沒有需要切換的擁有者。")
        return 0

    if not apply:
        print(f"會被切換的擁有者 {len(owner_ids)} 位（加 --apply 才寫入）：")
        for owner_id in owner_ids:
            print(f"  {owner_id}")
        return 0

    switched = 0
    for owner_id in owner_ids:
        if await enforce_if_assignment_complete(FamilyTreeRepository, owner_id):
            switched += 1
            print(f"  {owner_id}  → enforced")
        else:
            print(f"  {owner_id}  略過（重新判定後不符合）")
    print(f"已切換 {switched} 位。")
    return 0


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="實際寫入；省略時只列出")
    args = parser.parse_args()

    if not os.getenv("MONGODB_URI"):
        print("未設定 MONGODB_URI，無法讀取族譜。", file=sys.stderr)
        return 1
    return asyncio.run(run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
