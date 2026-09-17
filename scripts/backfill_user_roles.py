#!/usr/bin/env python3
"""把指定使用者的 role 設成 admin（或其他角色）。

以前這支腳本沒有任何參數，跑一次就把**全體**使用者都設成 admin——admin 能看
所有人的知識回報審核佇列，這等於一個手滑就把後台開給每一位長輩。現在：

  * 預設只讀不寫（dry-run），印出每一位會被改動的使用者與前後值。
  * 對象要明說：`--line-id U...`（可重複）只改這幾位；`--all` 才是全體。
  * `--all` 要寫入時除了 `--apply` 還要在提示時打出 `ALL`，兩道確認都過才動。

用法
────
    .venv/bin/python scripts/backfill_user_roles.py --line-id Uaaa --line-id Ubbb
    .venv/bin/python scripts/backfill_user_roles.py --line-id Uaaa --apply
    .venv/bin/python scripts/backfill_user_roles.py --all             # 只列出
    .venv/bin/python scripts/backfill_user_roles.py --all --apply     # 會要求打 ALL
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Iterable, Optional

import pymongo
from dotenv import load_dotenv


DEFAULT_DB_NAME = "CARE_database"
DEFAULT_COLLECTION_NAME = "users"
DEFAULT_ROLE = "admin"
ALL_CONFIRMATION = "ALL"


def _get_mongodb_uri() -> str:
    uri = os.getenv("MONGODB_URI") or os.getenv("MONGODB_URL")
    if not uri:
        raise RuntimeError("Missing MONGODB_URI or MONGODB_URL")
    return uri


def plan_role_changes(docs: Iterable[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    """哪些文件的 role 會變。已經是目標角色的不列，沒有 line_id 的也不列。

    純函式、不碰資料庫，讓判斷可以被單元測試釘住。
    """
    changes: list[dict[str, Any]] = []
    for doc in docs:
        line_id = doc.get("line_id")
        if not line_id:
            continue
        current = doc.get("role")
        if current == role:
            continue
        changes.append({"_id": doc.get("_id"), "line_id": line_id, "from": current, "to": role})
    return changes


def build_query(line_ids: Optional[list[str]]) -> dict[str, Any]:
    """`--line-id` 給了就只查那幾位；沒給（`--all`）就是全體。"""
    if line_ids:
        return {"line_id": {"$in": list(dict.fromkeys(line_ids))}}
    return {}


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        "--line-id",
        action="append",
        dest="line_ids",
        metavar="LINE_ID",
        help="要改的使用者（可重複）",
    )
    target.add_argument("--all", action="store_true", help="全體使用者（寫入時要再打 ALL 確認）")
    parser.add_argument("--role", default=DEFAULT_ROLE, help=f"目標角色（預設 {DEFAULT_ROLE}）")
    parser.add_argument("--apply", action="store_true", help="真的寫入；預設只列出會改什麼")
    return parser.parse_args(argv)


def confirm_all(prompt_input=None) -> bool:
    """`--all --apply` 的第二道確認：要打出 ALL（大小寫都要對）才算。"""
    answer = (prompt_input or input)(
        f"這會改動**全體**使用者的 role。確定的話請輸入 {ALL_CONFIRMATION}："
    )
    return answer.strip() == ALL_CONFIRMATION


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    load_dotenv()
    uri = _get_mongodb_uri()
    db_name = os.getenv("MONGODB_DB", DEFAULT_DB_NAME)
    collection_name = os.getenv("MONGODB_USERS_COLLECTION", DEFAULT_COLLECTION_NAME)

    client = pymongo.MongoClient(uri)
    collection = client[db_name][collection_name]

    query = build_query(args.line_ids)
    docs = list(collection.find(query, {"line_id": 1, "role": 1}))
    changes = plan_role_changes(docs, args.role)

    if args.line_ids:
        found = {doc.get("line_id") for doc in docs}
        for line_id in args.line_ids:
            if line_id not in found:
                print(f"找不到使用者：{line_id}")

    print(f"db={db_name} collection={collection_name} matched={len(docs)} to_change={len(changes)}")
    for change in changes:
        print(f"  {change['line_id']}: {change['from']!r} -> {change['to']!r}")

    if not args.apply:
        print("dry-run：沒有寫入。要寫入請加 --apply。")
        return 0
    if not changes:
        print("沒有需要改動的使用者。")
        return 0
    if args.all and not confirm_all():
        print("未確認，沒有寫入。")
        return 1

    result = collection.update_many(
        {"_id": {"$in": [change["_id"] for change in changes]}},
        {"$set": {"role": args.role}},
    )
    print(f"matched={result.matched_count} modified={result.modified_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
