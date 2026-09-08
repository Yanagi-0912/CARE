#!/usr/bin/env python3
"""掃描向量庫裡所有來源網址，統計死鏈的實際比例。

為什麼要有這支：`link_check.py` 只在回答出口降級，庫裡的死 url 原封不動
留著——每次被檢索到就再降級一次。要決定值不值得回頭清庫（重新 ingest 或
下架該來源），得先知道死鏈佔多少、影響多少 chunk。這支就是拿那個數字。

判定邏輯完全沿用 `app.services.rag.link_check`，不另外寫一套：稽核報告說
「這條是死的」，必須與線上真的會降級的那條規則一模一樣，否則報告沒有意義。
信任鏈也一樣走 `app.core.ca_bundle`（certifi ＋ 釘選的 TWCA 中繼憑證），
否則 www.mohw.gov.tw／www.hpa.gov.tw 會驗不過而全部落進「判不出來」。
判死的門檻很高（只有 404/410 與連不上），403、5xx、TLS 驗不過都算「判不
出來」，理由見該模組。

**唯讀。** 不刪除、不修改任何資料——要不要清庫是看完數字之後的決定，不是
這支腳本的職權。

用法：
  python scripts/audit_source_links.py                    # 掃全庫
  python scripts/audit_source_links.py --limit 50         # 只掃 50 個網址（試跑）
  python scripts/audit_source_links.py --out report.json  # 另存完整結果
  python scripts/audit_source_links.py --timeout 15 --concurrency 12
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

import httpx
from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings
from app.services.rag.link_check import USER_AGENT, LinkChecker

# 稽核不在使用者等待路徑上，逾時可以比線上的 3s 寬——這裡寧可多等，也不要
# 因為對方站台慢就把它記成死鏈，那會直接汙染我們要拿的那個比例。
DEFAULT_TIMEOUT_SECONDS = 10.0
# 全域併發上限。真正的節流靠 --per-host，這個值只是總量的天花板。
DEFAULT_CONCURRENCY = 24
# 同一網域的併發上限。這不是禮貌設定，是報告正確性的前提：
#
# 2026-09-02 首次全庫掃描（全域併發 8、無 per-host 限制）把 46 個
# www.fda.gov.tw 的網址記成「判不出來」。逐一、間隔 1 秒重測同一批，
# 6/6 全部回 200——那 46 筆是我們自己打太快觸發對方節流造成的假象，
# 不是那些頁面真的有問題。稽核報告若把自己造成的雜訊算進去，拿到的
# 比例就是錯的。
DEFAULT_PER_HOST = 3


async def collect_sources(collection) -> list[dict]:
    """彙總庫裡每個 url 的 chunk 數與來源名。

    以 aggregate 在 server 端 group，不是把整個 collection 拉回本機再算：
    這支要能對上正式庫跑，chunk 數量級是十萬起跳。
    """
    pipeline = [
        {"$match": {"url": {"$type": "string", "$ne": ""}}},
        {
            "$group": {
                "_id": "$url",
                "chunks": {"$sum": 1},
                "source_name": {"$first": "$source_name"},
                "title": {"$first": "$original_title"},
            }
        },
        {"$sort": {"chunks": -1}},
    ]
    rows = await collection.aggregate(pipeline).to_list(length=None)
    return [
        {
            "url": row["_id"],
            "chunks": row["chunks"],
            "source_name": str(row.get("source_name") or "").strip(),
            "title": str(row.get("title") or "").strip(),
        }
        for row in rows
    ]


def _insecure_client(timeout: float) -> httpx.AsyncClient:
    """關閉憑證驗證的 client，**只給稽核用，而且平常不該需要**。

    預設路徑（LinkChecker 自建的 client）走 `app.core.ca_bundle`，也就是
    certifi ＋ 釘選的 TWCA 中繼憑證，www.mohw.gov.tw 與 www.hpa.gov.tw 都
    驗得過——那是真正的驗證，不是放行，永遠優先用它。

    留這個旗標是為了診斷：當某批網址開始落進「判不出來」時，用它跑一次就能
    分辨「憑證鏈的問題」與「站台真的有事」。放行憑證在這個用途下是可接受的
    取捨，因為本腳本唯讀、只看狀態碼、不讀也不儲存回應內容，由人手動執行，
    結果只進報告。**不要拿這個旗標的結果當作線上會有的行為。**
    """
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        verify=False,
    )


async def _check_grouped_by_host(
    checker: LinkChecker, urls: list[str], per_host: int
) -> dict[str, bool]:
    """依網域分組檢查：不同網域平行，同一網域一次最多 *per_host* 個。

    不是直接把整批丟給 checker：庫裡四成網址集中在 www.hpa.gov.tw，整批
    平行等於對單一站台猛打，觸發節流後那些回應會被記成「判不出來」而汙染
    報告（見 DEFAULT_PER_HOST 的實測）。分組後總時間由最大的那個網域決定，
    反而比無差別平行更快——網域之間本來就不必互相等待。
    """
    grouped: dict[str, list[str]] = {}
    for url in urls:
        grouped.setdefault(urlparse(url).netloc, []).append(url)

    async def drain(host_urls: list[str]) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for start in range(0, len(host_urls), per_host):
            out.update(await checker.alive(host_urls[start : start + per_host]))
        return out

    batches = await asyncio.gather(*(drain(g) for g in grouped.values()))
    merged: dict[str, bool] = {}
    for batch in batches:
        merged.update(batch)
    return merged


async def audit(
    sources: list[dict],
    *,
    timeout: float,
    concurrency: int,
    per_host: int = DEFAULT_PER_HOST,
    insecure: bool = False,
) -> list[dict]:
    client = _insecure_client(timeout) if insecure else None
    checker = LinkChecker(
        timeout_seconds=timeout,
        max_concurrency=concurrency,
        # 稽核是一次性的全量掃描，快取只會擋住重複的 url（本來就已經 group
        # 過了），把上限開到覆蓋整批，避免掃到一半開始互相驅逐。
        max_cache_entries=max(len(sources), 1),
        http_client=client,
    )
    try:
        statuses = await _check_grouped_by_host(
            checker, [s["url"] for s in sources], per_host
        )
    finally:
        if client is not None:
            await client.aclose()
    out = []
    for source in sources:
        verdict = statuses.get(source["url"])
        out.append(
            {
                **source,
                "verdict": (
                    "alive" if verdict is True
                    else "dead" if verdict is False
                    else "inconclusive"
                ),
            }
        )
    return out


def summarize(results: list[dict]) -> dict:
    buckets = {"alive": [], "dead": [], "inconclusive": []}
    for row in results:
        buckets[row["verdict"]].append(row)
    return {
        key: {
            "urls": len(rows),
            "chunks": sum(r["chunks"] for r in rows),
        }
        for key, rows in buckets.items()
    } | {"buckets": buckets}


def _pct(part: int, whole: int) -> str:
    return f"{(100.0 * part / whole):.1f}%" if whole else "—"


def _display_width(text: str) -> int:
    """字串在等寬終端佔的欄數：CJK 全形字算兩欄。

    不能用 len()：報告的欄位標題全是中文，f-string 的 `:<12` 數的是字元數，
    直接套會讓每一列的欄位對不齊。
    """
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _ljust(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _rjust(text: str, width: int) -> str:
    return " " * max(0, width - _display_width(text)) + text


def print_report(results: list[dict], elapsed: float) -> None:
    summary = summarize(results)
    buckets = summary.pop("buckets")
    total_urls = len(results)
    total_chunks = sum(r["chunks"] for r in results)

    print(f"\n{'':=<72}")
    print(f"來源網址稽核結果（{elapsed:.1f}s）")
    print(f"{'':=<72}")
    print(
        _ljust("判定", 12)
        + _rjust("網址數", 9)
        + _rjust("佔比", 10)
        + _rjust("chunk 數", 12)
        + _rjust("佔比", 10)
    )
    print(f"{'':-<72}")
    for key, label in (
        ("alive", "活"),
        ("dead", "死"),
        ("inconclusive", "判不出來"),
    ):
        stats = summary[key]
        print(
            _ljust(label, 12)
            + _rjust(str(stats["urls"]), 9)
            + _rjust(_pct(stats["urls"], total_urls), 10)
            + _rjust(str(stats["chunks"]), 12)
            + _rjust(_pct(stats["chunks"], total_chunks), 10)
        )
    print(f"{'':-<72}")
    print(
        _ljust("合計", 12)
        + _rjust(str(total_urls), 9)
        + _rjust("", 10)
        + _rjust(str(total_chunks), 12)
    )

    dead = sorted(buckets["dead"], key=lambda r: -r["chunks"])
    if dead:
        print(f"\n死鏈（{len(dead)} 個，依受影響 chunk 數排序）")
        print(f"{'':-<72}")
        for row in dead:
            name = row["source_name"] or row["title"] or "（無來源名）"
            print(
                f"  {row['chunks']:>5} chunk  "
                + _ljust(name[:20], 26)
                + row["url"]
            )
        print(
            "\n這些 url 在線上會被降級：知識庫路徑改顯示「來源名｜標題」而不給"
            "\n按鈕，網搜路徑整筆不顯示。要不要回頭清庫是另一個決定。"
        )
    else:
        print("\n沒有判定為死鏈的來源。")

    inconclusive = buckets["inconclusive"]
    if inconclusive:
        print(
            f"\n判不出來的有 {len(inconclusive)} 個（TLS 驗不過、403／429、5xx、"
            "重導迴圈）。"
            "\n這些在線上照常顯示連結——不是死鏈，是我們這端拿不到證據。"
        )


async def _main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--limit", type=int, default=0, help="只掃前 N 個網址（試跑用）")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument(
        "--per-host",
        type=int,
        default=DEFAULT_PER_HOST,
        help="同一網域的併發上限；調高會讓對方節流，報告出現假的「判不出來」",
    )
    parser.add_argument("--out", type=Path, help="把完整結果另存成 JSON")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help=(
            "診斷用：不驗證 TLS 憑證，藉此分辨「憑證鏈問題」與「站台真的有事」。"
            "預設已走 ca_bundle（certifi ＋ TWCA 中繼憑證）做真正的驗證，"
            "平常不需要這個旗標。"
        ),
    )
    args = parser.parse_args()

    if not settings.MONGODB_URI or not settings.MONGODB_COLLECTION:
        print("MONGODB_URI／MONGODB_COLLECTION 未設定，無法連線。", file=sys.stderr)
        return 2

    client = AsyncIOMotorClient(settings.MONGODB_URI)
    try:
        collection = client[settings.MONGODB_DB][settings.MONGODB_COLLECTION]
        print(f"讀取 {settings.MONGODB_DB}.{settings.MONGODB_COLLECTION} 的來源網址…", flush=True)
        sources = await collect_sources(collection)
        if not sources:
            print("庫裡沒有帶 url 的文件。")
            return 0
        if args.limit > 0:
            sources = sources[: args.limit]

        total_chunks = sum(s["chunks"] for s in sources)
        tls_note = "、不驗憑證" if args.insecure else ""
        print(
            f"共 {len(sources)} 個相異網址、{total_chunks} 個 chunk，"
            f"開始檢查（逾時 {args.timeout}s、每網域併發 {args.per_host}"
            f"、總併發 {args.concurrency}{tls_note}）…",
            flush=True,
        )
        t0 = time.perf_counter()
        results = await audit(
            sources,
            timeout=args.timeout,
            concurrency=args.concurrency,
            per_host=args.per_host,
            insecure=args.insecure,
        )
        elapsed = time.perf_counter() - t0
    finally:
        client.close()

    print_report(results, elapsed)

    if args.out:
        args.out.write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n完整結果已寫入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
