#!/usr/bin/env python3
"""把 users 文件的健康欄位從中文字串格式遷移到語言中立的 code 格式。

背景
----
慢性病史原本存成一個以「、」串起來的中文字串（例：「高血壓、痛風」），裡面同時
混著兩種性質完全不同的資料：固定選項（要依使用者語言翻譯）與使用者自行輸入的
病名（必須原文照留）。混在一起就分不出哪個是哪個，讀取端只能猜。

本腳本把它拆成兩欄，並把性別換成與前端 i18n key 同名的 code：

    gender: "男"                      -> "male"
    chronic_history: "高血壓、痛風"    -> chronic_diseases: ["hypertension"]
                                         chronic_custom:   ["痛風"]
    major_illness_history: "無"       -> ""

執行順序很重要
--------------
必須與後端部署一起完成，不可分兩次上線。舊後端讀不到新欄位、新後端讀不到舊欄位，
中間任何一段時間差都會讓 AI 的 prompt 出現「慢性病史：無」——那是一句合法通順的
臨床斷言，LLM 無從分辨它是「確認沒有」還是「資料讀不到」。

用法
----
    python scripts/migrate_chronic_and_gender.py            # dry-run，只印出會改什麼
    python scripts/migrate_chronic_and_gender.py --apply    # 實際寫入

預設是 dry-run。這支腳本會動到病歷資料，先看過再寫。可重複執行：已遷移的文件
沒有 chronic_history，會被自動略過。
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from typing import Any

import pymongo
from dotenv import load_dotenv
from pymongo import UpdateOne

DEFAULT_DB_NAME = "CARE_database"
DEFAULT_COLLECTION_NAME = "users"

CHRONIC_SEPARATOR = "、"
NONE_VALUE = "無"

# 中文病名 -> code。code 與前端 i18n key 的最後一段、以及 nodes.py 的
# _CHRONIC_CODE_LABELS 完全對應，三處必須一致。
CHRONIC_NAME_TO_CODE = {
    "高血壓": "hypertension",
    "糖尿病": "diabetes",
    "高血脂": "hyperlipidemia",
    "心臟病": "heartDisease",
    "腎臟病": "kidneyDisease",
    "氣喘": "asthma",
    "慢性阻塞性肺病": "copd",
    "癌症": "cancer",
}

# 歷史上這個欄位出現過好幾種寫法（'男'、'女性'、'male'…），一律收斂到三個值。
GENDER_TO_CODE = {
    "男": "male",
    "男性": "male",
    "male": "male",
    "女": "female",
    "女性": "female",
    "female": "female",
    "unknown": "unknown",
}


def split_chronic(chronic_history: str) -> tuple[list[str], list[str]]:
    """「高血壓、痛風」-> (["hypertension"], ["痛風"])。

    認得的病名轉成 code，其餘視為使用者自行輸入原樣保留。「無」不是病名，
    是空值的哨兵，直接丟掉——空陣列本身就表示沒有。
    """
    codes: list[str] = []
    custom: list[str] = []

    for part in chronic_history.split(CHRONIC_SEPARATOR):
        name = part.strip()
        if not name or name == NONE_VALUE:
            continue
        code = CHRONIC_NAME_TO_CODE.get(name)
        if code:
            codes.append(code)
        else:
            custom.append(name)

    return codes, custom


def build_update(doc: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """回傳這份文件要下的 ($set, $unset)。兩者皆空代表不需要動。"""
    set_fields: dict[str, Any] = {}
    unset_fields: dict[str, Any] = {}

    chronic_history = doc.get("chronic_history")
    if isinstance(chronic_history, str):
        codes, custom = split_chronic(chronic_history)
        set_fields["chronic_diseases"] = codes
        set_fields["chronic_custom"] = custom
        # $set 不會移除舊欄位。不明確 $unset 的話，改名後的 chronic_history
        # 會永遠留在文件裡，成為一個不再更新卻看起來仍然有效的殭屍欄位。
        unset_fields["chronic_history"] = ""

    # 認不得的性別收斂成 unknown：model 已改為 Literal，留著奇怪的值會讓
    # 使用者下次儲存時被擋在 422，而且自己完全看不出原因。
    gender = doc.get("gender")
    if isinstance(gender, str):
        code = GENDER_TO_CODE.get(gender.strip(), "unknown")
        if code != gender:
            set_fields["gender"] = code

    # 「無」是塞在自由輸入欄位裡的中文哨兵值，空字串才是它真正的意思。
    for field in ("major_illness_history", "surgery_history"):
        value = doc.get(field)
        if isinstance(value, str) and value.strip() == NONE_VALUE:
            set_fields[field] = ""

    return set_fields, unset_fields


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="實際寫入。未指定時只做 dry-run，不會動到任何資料。",
    )
    args = parser.parse_args()

    load_dotenv()
    uri = os.getenv("MONGODB_URI") or os.getenv("MONGODB_URL")
    if not uri:
        raise RuntimeError("Missing MONGODB_URI or MONGODB_URL")

    db_name = os.getenv("MONGODB_DB", DEFAULT_DB_NAME)
    collection_name = os.getenv("MONGODB_USERS_COLLECTION", DEFAULT_COLLECTION_NAME)

    client = pymongo.MongoClient(uri)
    collection = client[db_name][collection_name]

    operations: list[UpdateOne] = []
    unmapped_genders: Counter[str] = Counter()
    custom_names: Counter[str] = Counter()
    stats = Counter()

    total = collection.count_documents({})
    for doc in collection.find({}):
        stats["scanned"] += 1
        set_fields, unset_fields = build_update(doc)
        if not set_fields and not unset_fields:
            stats["already_migrated"] += 1
            continue

        if "chronic_diseases" in set_fields:
            stats["chronic_split"] += 1
            custom_names.update(set_fields["chronic_custom"])
        if "gender" in set_fields:
            stats["gender_recoded"] += 1
            original = (doc.get("gender") or "").strip()
            if original not in GENDER_TO_CODE:
                unmapped_genders[original] += 1

        update: dict[str, Any] = {}
        if set_fields:
            update["$set"] = set_fields
        if unset_fields:
            update["$unset"] = unset_fields
        operations.append(UpdateOne({"_id": doc["_id"]}, update))

    print(f"total_users={total}")
    for key in ("scanned", "already_migrated", "chronic_split", "gender_recoded"):
        print(f"{key}={stats[key]}")
    print(f"to_update={len(operations)}")

    # 自訂病名會原樣搬進 chronic_custom，不做任何轉換。列出來是為了讓執行的人
    # 一眼看出有沒有本來就該進固定選項、卻因為錯字或別名而落到自訂的病名。
    if custom_names:
        print("\ncustom_disease_names (原樣保留，未轉成 code):")
        for name, count in custom_names.most_common():
            print(f"  {name} x{count}")

    if unmapped_genders:
        print("\nUNMAPPED gender values (將被改成 'unknown'，使用者需重新選擇):")
        for value, count in unmapped_genders.most_common():
            print(f"  {value!r} x{count}")

    if not args.apply:
        print("\nDRY-RUN：未寫入任何資料。確認上面的結果後加上 --apply 才會實際執行。")
        return

    if not operations:
        print("\n沒有需要更新的文件。")
        return

    result = collection.bulk_write(operations, ordered=False)
    print(f"\nmatched={result.matched_count}")
    print(f"modified={result.modified_count}")


if __name__ == "__main__":
    main()
