#!/usr/bin/env python3
"""替由「知識回報」收錄的知識庫文件補上頁面自己標示的發布日期。

為什麼需要這一支：`IngestService` 直到 2026-09-27 才把 `published_at` 寫進去，
在那之前由這條路徑收錄的文件完全沒有日期欄位（線上 22 篇、293 個切片）。ETL
那邊有自癒機制會逐輪補齊既有資料，收錄路徑沒有對應的東西——它只在 admin 核准
新回報時才會跑，既有文件再也不會被碰到。

日期從**已經存在庫裡的內文**抽（`extract_stated_publish_date`），不重新抓取：
要補的是那份當時被核准的內容自己說的日期，重抓會抓到頁面現在的樣子，而且要
再花一次 Firecrawl 額度。抽不出來的文章就維持沒有日期——多數醫院衛教頁整頁
沒有任何日期標示（實測 22 篇裡 18 篇），那是實情。

預設只讀不寫，加 `--apply` 才真的寫入。

用法
────
    .venv/bin/python scripts/backfill_ingested_publish_dates.py
    .venv/bin/python scripts/backfill_ingested_publish_dates.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys

import pymongo
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.publish_date import extract_stated_publish_date  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="真的寫入（預設只列出）")
    args = parser.parse_args()

    load_dotenv()
    uri = os.getenv("MONGODB_URI")
    db_name = os.getenv("MONGODB_DB")
    collection_name = os.getenv("MONGODB_COLLECTION")
    if not (uri and db_name and collection_name):
        print("錯誤：MONGODB_URI／MONGODB_DB／MONGODB_COLLECTION 需在 .env 設定")
        return 1

    client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=20000)
    collection = client[db_name][collection_name]

    # 只看收錄路徑寫進來的（有 ingested_at）、且還沒有日期的文件。
    # ETL 寫的文件不在範圍內：那邊有自己的補日期機制，來源也不同。
    query = {"ingested_at": {"$exists": True}, "published_at": {"$in": [None, ""]}}
    by_url: dict[str, list[dict]] = {}
    for doc in collection.find(query, {"url": 1, "chunk_content": 1, "chunk_index": 1}):
        by_url.setdefault(doc.get("url", ""), []).append(doc)

    updated = skipped = 0
    for url, chunks in sorted(by_url.items()):
        # 整篇的內文接起來再抽：日期通常在最後一塊（頁尾）。
        text = "\n".join(
            str(c.get("chunk_content") or "")
            for c in sorted(chunks, key=lambda c: c.get("chunk_index", 0))
        )
        date = extract_stated_publish_date(text)
        if not date:
            skipped += 1
            continue
        print(f"  {date}  {url[:70]}（{len(chunks)} 個切片）")
        if args.apply:
            collection.update_many(
                {"url": url, "ingested_at": {"$exists": True}},
                {"$set": {"published_at": date}},
            )
        updated += 1

    verb = "已補上" if args.apply else "可補上（未寫入）"
    print(f"\n{len(by_url)} 篇：{verb} {updated} 篇，頁面沒有日期標示 {skipped} 篇")
    if not args.apply and updated:
        print("加 --apply 才會實際寫入。")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
