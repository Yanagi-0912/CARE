#!/usr/bin/env python3
"""清除以 Firecrawl 抓首頁而產生的導覽列噪音 chunk。

這批資料由 CARE 的 IngestService 對首頁 URL 執行 ingest 產生，內容是
「一站式搜尋」「## 主視覺與專區連結」「[跳到主要內容區塊]」這類導覽元素，
對醫療問答無檢索價值，且每筆都佔一個 3072 維向量。

刻意以「明列 URL」而非「content_hash 是否存在」為條件：後者會連帶刪除
未來由知識回報審核流程正常寫入的資料。

用法：
  python scripts/purge_navigation_chunks.py            # dry-run，只報告
  python scripts/purge_navigation_chunks.py --apply    # 實際刪除
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings

# 2026-08-08 實測：以下 URL 下的 chunk 全為網站導覽元素，無文章正文。
# https://www.hpa.gov.tw/... 為格式損毀的 URL（內容是客服電話清單）。
#
# pid=19922 例外說明：這個 URL 表面上長得跟正常文章頁一模一樣（golden set 裡
# pid=16550、pid=17435 都是正常文章頁），但實際查詢其下 37 筆 chunk，內容是
# 年份導覽清單與頁面骨架，不是該篇文章正文，例如：
#   '- [115年](https://www.hpa.gov.tw/Pages/TopicList.aspx?nodeid=5020 "115年")...'
#   '[跳到主要內容區塊](https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=5020&pid=19922#m'
#   '## [新聞](https://www.hpa.gov.tw/Pages/List.aspx?nodeid=124 "新聞")'
#   '[定位點](https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=5020&pid=19922#main-c'
# 也就是說 Firecrawl 對這個 Detail.aspx 網址抓到的是導覽骨架，不是文章內文。
NAVIGATION_URLS: tuple[str, ...] = (
    "https://www.mohw.gov.tw/",
    "https://www.fda.gov.tw/",
    "https://165.npa.gov.tw/",
    "https://www.hpa.gov.tw/",
    "https://www.hpa.gov.tw/...",
    "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=5020&pid=19922",
)

_SAMPLE_LIMIT = 2
_SNIPPET_LEN = 60


def build_delete_filter(urls: list[str] | tuple[str, ...]) -> dict:
    return {"url": {"$in": list(urls)}}


async def purge(
    collection, urls, *, apply: bool, text_field: str = "chunk_content"
) -> dict[str, int]:
    matched = 0
    for url in urls:
        count = await collection.count_documents({"url": url})
        print(f"  {count:>5}  {url}")
        if count:
            samples = await collection.find({"url": url}).limit(_SAMPLE_LIMIT).to_list(
                length=_SAMPLE_LIMIT
            )
            for doc in samples:
                snippet = str(doc.get(text_field) or "").strip()[:_SNIPPET_LEN]
                print(f"           例：{snippet!r}")
        matched += count

    deleted = 0
    if apply and matched:
        result = await collection.delete_many(build_delete_filter(urls))
        deleted = result.deleted_count

    return {"matched": matched, "deleted": deleted}


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="實際執行刪除；未指定時只報告不刪除",
    )
    args = parser.parse_args()

    client = AsyncIOMotorClient(settings.MONGODB_URI)
    collection = client[settings.MONGODB_DB][settings.MONGODB_COLLECTION]

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== 導覽列噪音清理（{mode}）===")
    report = await purge(
        collection,
        NAVIGATION_URLS,
        apply=args.apply,
        text_field=settings.MONGODB_TEXT_FIELD,
    )
    print(f"\n符合條件: {report['matched']} 筆")
    if args.apply:
        print(f"已刪除:   {report['deleted']} 筆")
    else:
        print("未刪除（加上 --apply 才會實際執行）")

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
