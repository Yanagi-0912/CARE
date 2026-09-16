#!/usr/bin/env python3
"""清掉 `users` 裡同一個 line_id 的重複文件，讓 line_id 的唯一索引建得起來。

背景
────
`users` 以前沒有 line_id 的唯一索引。LIFF 首次登入時前端會連打好幾支 API，
每一支都對同一位新使用者 upsert，併發下各插一筆——之後每一次
`find_one({"line_id"})` 拿到哪一筆看 Mongo 心情，使用者看到的健康資料時有時無，
每日消息卡也會同一個人送兩次。

`UserProfileRepository.ensure_indexes` 現在會在啟動時建唯一索引，但既有重複會讓
它建不起來（只留 log、不擋啟動）。這支腳本清完之後，下一次啟動索引就建得成。

它做什麼
────────
對每一個重複的 line_id：

  * 保留**最舊**的那一筆（created_at 最早；沒有 created_at 的排最後；同時間比
    _id）。與 `get_user_profile` 的排序一致，清完前後使用者看到的是同一份。
  * 把較新文件裡「保留那筆沒有或為空」的欄位補進保留那筆——空是指 None、""、
    []、{}。dict 欄位（settings、health_consultations）逐鍵補，不整包蓋。
    保留那筆已有值的欄位一律不動，較新文件的不同值只印出來給人看。
  * `updated_at` 取所有文件的最大值。
  * 刪掉其餘文件。

預設只讀不寫，印出完整計畫；帶 `--apply` 才寫入。

用法
────
    .venv/bin/python scripts/dedupe_users_line_id.py
    .venv/bin/python scripts/dedupe_users_line_id.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from typing import Any, Iterable, Optional

import pymongo
from dotenv import find_dotenv, load_dotenv


DEFAULT_DB_NAME = "CARE_database"
DEFAULT_COLLECTION_NAME = "users"

# 身分與時間欄位不參與合併：line_id 本來就相同，_id／created_at 跟著保留那筆走，
# updated_at 另外取最大值。
_NOT_MERGED = {"_id", "line_id", "created_at", "updated_at"}


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _sort_key(doc: dict[str, Any]) -> tuple[int, Any, str]:
    created_at = doc.get("created_at")
    # 沒有 created_at 的排最後（不可能是最早建立的那筆，那筆一定寫了時間戳）。
    missing = 0 if isinstance(created_at, datetime) else 1
    return (missing, created_at if missing == 0 else datetime.max, str(doc.get("_id")))


def merge_fields(keeper: dict[str, Any], newer_docs: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    """算出要補進保留那筆的欄位，以及被放棄的不同值（給人核對）。

    回傳 `(filled, dropped)`：`filled` 是 `$set` 的內容（含 dict 欄位的點記法鍵）；
    `dropped` 是「保留那筆已有值、較新文件有不同值」的欄位 → 被放棄的值清單。
    `newer_docs` 依新到舊排，先出現的優先補。
    """
    filled: dict[str, Any] = {}
    dropped: dict[str, list[Any]] = {}
    for doc in newer_docs:
        for key, value in doc.items():
            if key in _NOT_MERGED or _is_empty(value):
                continue
            current = keeper.get(key)
            if isinstance(value, dict) and (isinstance(current, dict) or _is_empty(current)):
                current_dict = current if isinstance(current, dict) else {}
                for sub_key, sub_value in value.items():
                    if _is_empty(sub_value):
                        continue
                    dotted = f"{key}.{sub_key}"
                    if dotted in filled:
                        continue
                    if _is_empty(current_dict.get(sub_key)):
                        filled[dotted] = sub_value
                    elif current_dict.get(sub_key) != sub_value:
                        dropped.setdefault(dotted, []).append(sub_value)
                continue
            if key in filled:
                continue
            if _is_empty(current):
                filled[key] = value
            elif current != value:
                dropped.setdefault(key, []).append(value)

    updated_ats = [
        doc.get("updated_at")
        for doc in [keeper, *newer_docs]
        if isinstance(doc.get("updated_at"), datetime)
    ]
    if updated_ats:
        latest = max(updated_ats)
        if keeper.get("updated_at") != latest:
            filled["updated_at"] = latest
    return filled, dropped


def plan_dedupe(docs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """把所有文件依 line_id 分組，對每個重複的組算出保留誰、補什麼、刪誰。

    純函式、不碰資料庫。回傳的每一項：
    `{"line_id", "keep_id", "delete_ids", "filled", "dropped"}`。
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        line_id = doc.get("line_id")
        if not line_id:
            continue
        groups.setdefault(line_id, []).append(doc)

    plans: list[dict[str, Any]] = []
    for line_id, members in groups.items():
        if len(members) < 2:
            continue
        ordered = sorted(members, key=_sort_key)
        keeper, rest = ordered[0], ordered[1:]
        filled, dropped = merge_fields(keeper, list(reversed(rest)))
        plans.append(
            {
                "line_id": line_id,
                "keep_id": keeper.get("_id"),
                "delete_ids": [doc.get("_id") for doc in rest],
                "filled": filled,
                "dropped": dropped,
            }
        )
    return plans


def _get_mongodb_uri() -> str:
    uri = os.getenv("MONGODB_URI") or os.getenv("MONGODB_URL")
    if not uri:
        raise RuntimeError("Missing MONGODB_URI or MONGODB_URL")
    return uri


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--apply", action="store_true", help="真的寫入；預設只列出計畫")
    return parser.parse_args(argv)


def apply_plans(collection: Any, plans: list[dict[str, Any]]) -> tuple[int, int]:
    """執行計畫。回傳 (補了欄位的文件數, 刪掉的文件數)。"""
    filled_docs = 0
    deleted = 0
    for plan in plans:
        if plan["filled"]:
            result = collection.update_one({"_id": plan["keep_id"]}, {"$set": plan["filled"]})
            filled_docs += int(getattr(result, "modified_count", 0) or 0)
        result = collection.delete_many({"_id": {"$in": plan["delete_ids"]}})
        deleted += int(getattr(result, "deleted_count", 0) or 0)
    return filled_docs, deleted


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    # find_dotenv 預設從呼叫端的檔案位置往上找，腳本經 stdin 串進 pod 執行
    # （`kubectl exec -i ... python - < script`）時沒有檔案位置，會 AssertionError。
    # 改從工作目錄找：本機在 repo 根目錄跑找得到 .env，pod 裡沒有 .env 也無妨，
    # 連線字串已在環境變數裡。
    load_dotenv(find_dotenv(usecwd=True))
    uri = _get_mongodb_uri()
    db_name = os.getenv("MONGODB_DB", DEFAULT_DB_NAME)
    collection_name = os.getenv("MONGODB_USERS_COLLECTION", DEFAULT_COLLECTION_NAME)

    client = pymongo.MongoClient(uri)
    collection = client[db_name][collection_name]

    # 只撈有重複的 line_id，再把那幾組的完整文件抓回來——全表掃描一次就夠。
    duplicated = [
        row["_id"]
        for row in collection.aggregate(
            [
                {"$match": {"line_id": {"$exists": True, "$ne": None, "$ne": ""}}},
                {"$group": {"_id": "$line_id", "n": {"$sum": 1}}},
                {"$match": {"n": {"$gt": 1}}},
            ]
        )
    ]
    docs = list(collection.find({"line_id": {"$in": duplicated}})) if duplicated else []
    plans = plan_dedupe(docs)

    print(f"db={db_name} collection={collection_name} duplicated_line_ids={len(plans)}")
    for plan in plans:
        print(f"line_id={plan['line_id']} keep={plan['keep_id']} delete={plan['delete_ids']}")
        for key, value in plan["filled"].items():
            print(f"  補 {key} = {value!r}")
        for key, values in plan["dropped"].items():
            print(f"  放棄 {key} 的其他值：{values!r}")

    total_delete = sum(len(plan["delete_ids"]) for plan in plans)
    print(f"to_delete={total_delete}")

    if not args.apply:
        print("dry-run：沒有寫入。要寫入請加 --apply。")
        return 0
    if not plans:
        print("沒有重複，不需要動。")
        return 0

    filled_docs, deleted = apply_plans(collection, plans)
    print(f"filled_docs={filled_docs} deleted={deleted}")
    print("清完了。重啟 backend／scheduler 讓 users 的 line_id 唯一索引建起來。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
