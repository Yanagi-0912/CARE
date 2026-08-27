# -*- coding: utf-8 -*-
"""
把 medicalFacilities.departments 裡的「家庭醫學科」統一改寫為「家醫科」。

用途說明：
    這是一支一次性遷移腳本，不需要排程執行。

    資料庫的 departments 同時存在「家醫科」與「家庭醫學科」兩個值，兩者在醫療上
    是同一科，但 build_department_query() 用的是 regex 逐元素比對，而「家醫科」
    不是「家庭醫學科」的連續子字串——查其中一種寫法會靜默漏掉另一種寫法的院所。
    本腳本把資料源頭統一，讓應用層不必再維護等價寫法對照。

    執行後 department_matcher 的 DEPARTMENT_ALIASES 仍保留
    「家庭醫學科 → 家醫科」，因為使用者還是會這樣講；改的只是資料庫的值。

兩種需要處理的資料形態：
    1. 正常：departments 為 ["不分科", "家庭醫學科"]，逐元素比對即可。
    2. 髒資料：約 12 筆院所（多為醫學中心）把整串科別塞進單一元素，例如
       ["家庭醫學科、內科、外科、兒科"]。這種要做元素內的字串取代，
       只比對整個元素會漏掉。

去重：
    同時含「家醫科」與「家庭醫學科」的文件改寫後會出現重複值，需去除。
    去重時保留原本的順序，不用 set()——順序在 Flex 卡片上是看得到的。

安全性：
    預設為 dry-run，只印出將要變更的內容不寫入。確認無誤後加 --apply 才實際寫入。
    腳本可重複執行（idempotent）：已改寫過的資料不會被再次計入或修改。

用法：
    python scripts/normalize_family_medicine_department.py           # dry-run
    python scripts/normalize_family_medicine_department.py --apply   # 實際寫入
    python scripts/normalize_family_medicine_department.py --apply --limit 10
"""

import argparse

import pymongo
from dotenv import load_dotenv
from pymongo import UpdateOne

OLD_VALUE = "家庭醫學科"
NEW_VALUE = "家醫科"

COLLECTION_NAME = "medicalFacilities"

# 一次送出的 bulk_write 筆數。太小會來回太多次，太大則單次失敗要重跑的量太多。
BATCH_SIZE = 500


def normalize_departments(departments: list) -> list | None:
    """
    回傳改寫後的 departments；不需要變更時回傳 None。

    非字串元素原樣保留——資料清理不是這支腳本的職責，把不認得的東西丟掉
    是比留著更糟的事。
    """
    changed = False
    rewritten: list = []

    for item in departments:
        if not isinstance(item, str):
            rewritten.append(item)
            continue

        if OLD_VALUE in item:
            # 涵蓋「家庭醫學科」與「家庭醫學科、內科、外科」兩種形態
            new_item = item.replace(OLD_VALUE, NEW_VALUE)
            changed = True
            rewritten.append(new_item)
        else:
            rewritten.append(item)

    if not changed:
        return None

    # 去重但保留順序：原本同時有「家醫科」「家庭醫學科」的文件改寫後會重複。
    deduped: list = []
    for item in rewritten:
        if item not in deduped:
            deduped.append(item)

    return deduped


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="把 medicalFacilities.departments 的「家庭醫學科」統一為「家醫科」"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="實際寫入資料庫。未指定時為 dry-run，只列出將要變更的內容。",
    )
    parser.add_argument(
        "--db",
        default="CARE_database",
        help="資料庫名稱（預設 CARE_database）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="只處理前 N 筆，用於小量試跑。0 表示不限制。",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    load_dotenv()

    from app.dependencies import get_mongodb_uri

    try:
        mongodb_uri = get_mongodb_uri()
    except ValueError as exc:
        print(f"錯誤：{exc}，請確認 .env 的 MONGODB_URI 設定。")
        return

    print("正在連線至 MongoDB...")
    client = pymongo.MongoClient(mongodb_uri)
    collection = client[args.db][COLLECTION_NAME]
    print(f"成功連線：{args.db}.{COLLECTION_NAME}")

    # 用 regex 而非精確比對，才能一併撈到「家庭醫學科、內科、…」的髒資料。
    query = {"departments": {"$regex": OLD_VALUE}}
    total = collection.count_documents(query)
    print(f"\n找到 {total} 筆含「{OLD_VALUE}」的文件。")

    if total == 0:
        print("沒有需要處理的資料（可能已執行過本腳本）。")
        client.close()
        return

    mode = "實際寫入" if args.apply else "dry-run（不寫入）"
    print(f"模式：{mode}\n")

    cursor = collection.find(query, {"name": 1, "departments": 1})
    if args.limit:
        cursor = cursor.limit(args.limit)

    operations: list[UpdateOne] = []
    planned = 0
    dirty_rows = 0
    deduped_rows = 0
    modified = 0

    for doc in cursor:
        departments = doc.get("departments") or []
        if not isinstance(departments, list):
            print(f"  跳過（departments 非陣列）：{doc.get('name')}")
            continue

        rewritten = normalize_departments(departments)
        if rewritten is None:
            continue

        planned += 1
        if any(isinstance(x, str) and "、" in x for x in departments):
            dirty_rows += 1
        if len(rewritten) < len(departments):
            deduped_rows += 1

        # dry-run 全部列出；實際寫入時只印前 20 筆，避免洗版蓋掉最後的統計。
        if not args.apply or planned <= 20:
            print(f"  {doc.get('name')}")
            print(f"    - {departments}")
            print(f"    + {rewritten}")

        operations.append(
            UpdateOne({"_id": doc["_id"]}, {"$set": {"departments": rewritten}})
        )

        if args.apply and len(operations) >= BATCH_SIZE:
            modified += collection.bulk_write(operations).modified_count
            operations = []

    if args.apply and operations:
        modified += collection.bulk_write(operations).modified_count

    print("\n" + "=" * 50)
    print(f"待變更文件：{planned} 筆")
    print(f"  其中整串科別擠在單一元素的髒資料：{dirty_rows} 筆")
    print(f"  其中改寫後需去除重複值的：{deduped_rows} 筆")

    if args.apply:
        print(f"實際更新：{modified} 筆")
        remaining = collection.count_documents(query)
        print(f"驗證：資料庫剩餘含「{OLD_VALUE}」的文件 {remaining} 筆")
        if remaining and not args.limit:
            print("警告：仍有殘留，請檢查上方是否有被跳過的文件。")
    else:
        print("未寫入任何資料。確認無誤後加上 --apply 執行。")

    client.close()


if __name__ == "__main__":
    main()
