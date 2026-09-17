"""量網搜這一段的耗時：搜尋、每筆 scrape，以及序列與並行的落差。

跑法：
    .venv/bin/python scripts/web_search_latency_bench.py            # golden set 的 web 題
    .venv/bin/python scripts/web_search_latency_bench.py -n 5       # 只跑前 5 題
    .venv/bin/python scripts/web_search_latency_bench.py -q "問句"  # 指定問句

**這會打真實的 Firecrawl API、算額度。** 每題最多 1 次 search（2 credits）＋
CITE_TOP_K 次 scrape（各 1 credit），也就是每題 ≤ 5 credits。

量法上的一個取捨：序列耗時是**推估**，不是實測。每個網址只抓一次（實測各自
的 ms），並行那一欄是那一批的實測牆鐘時間，序列那一欄是「照原順序把該抓的
ms 加起來、湊滿 CITE_TOP_K 就停」。不重跑一次序列版是因為 Firecrawl 會快取
scrape 結果，第二輪跑的那個變體會平白變快，兩個數字就不能比了。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from app.core.config import settings  # noqa: E402
from app.services.rag.firecrawl_client import FirecrawlClient  # noqa: E402
from app.services.rag.web_search_service import (  # noqa: E402
    CITE_TOP_K,
    WEB_SEARCH_LIMIT,
    WEB_SNIPPET_MIN_CHARS,
)
from app.services.rag.whitelist import (  # noqa: E402
    is_allowed_url,
    normalize_url,
    with_whitelist_site_filter,
)

GOLDEN = Path(__file__).resolve().parents[1] / "evals" / "rag" / "golden.jsonl"


@dataclass
class QueryResult:
    query: str
    search_ms: float = 0.0
    hits: int = 0
    candidates: int = 0
    snippet_ok: int = 0
    scraped: int = 0
    scrape_parallel_ms: float = 0.0
    per_url_ms: dict[str, float] = field(default_factory=dict)
    per_url_chars: dict[str, int] = field(default_factory=dict)
    order: list[tuple[str, int]] = field(default_factory=list)  # (url, snippet_len)
    error: str = ""

    @property
    def scrape_sequential_ms(self) -> float:
        """舊版逐一 await 時這一段會花多久（由實測的單筆 ms 相加推估）。

        照原順序走，snippet 夠長的不抓、不計時；snippet 太短的加上它實測的
        ms；湊滿 CITE_TOP_K 份就停——與舊版的 early break 一致。

        這個數字是**偏低估**的：視窗外的候選沒有實測 ms（本來就不抓），在這裡
        一律算 0，而舊版真的會一路往下抓。低估的方向對新版不利，所以實際省下
        的只會比這裡印的更多，不會更少。
        """
        total = 0.0
        docs = 0
        for url, snippet_len in self.order:
            if snippet_len < WEB_SNIPPET_MIN_CHARS:
                total += self.per_url_ms.get(url, 0.0)
                if not self.per_url_chars.get(url, 0) and snippet_len == 0:
                    continue  # 抓不到又沒 snippet：舊版會跳過、繼續往下抓
            docs += 1
            if docs >= CITE_TOP_K:
                break
        return total


def load_queries(limit: int | None) -> list[str]:
    rows = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]
    queries = [r["query"] for r in rows if r.get("route") == "web"]
    return queries[:limit] if limit else queries


async def measure(client: FirecrawlClient, query: str) -> QueryResult:
    out = QueryResult(query=query)

    started = time.perf_counter()
    try:
        hits = await client.search(
            with_whitelist_site_filter(query), limit=WEB_SEARCH_LIMIT
        )
    except Exception as exc:  # 限流／逾時／5xx 都記下來，不要讓整輪停掉
        out.search_ms = (time.perf_counter() - started) * 1000
        out.error = f"search failed: {type(exc).__name__}: {exc}"
        return out
    out.search_ms = (time.perf_counter() - started) * 1000
    out.hits = len(hits)

    # 與 WebSearchService._candidates 同一套過濾：正規化、白名單、去重、保序
    seen: set[str] = set()
    for hit in hits:
        url = normalize_url((hit.url or "").strip())
        if url is None or url in seen or not is_allowed_url(url):
            continue
        seen.add(url)
        out.order.append((url, len((hit.description or "").strip())))
    out.candidates = len(out.order)
    out.snippet_ok = sum(
        1 for _url, n in out.order if n >= WEB_SNIPPET_MIN_CHARS
    )

    window = out.order[:CITE_TOP_K]
    to_scrape = [url for url, n in window if n < WEB_SNIPPET_MIN_CHARS]
    out.scraped = len(to_scrape)
    if not to_scrape:
        return out

    async def one(url: str) -> None:
        t0 = time.perf_counter()
        try:
            text = (await client.scrape(url) or "").strip()
        except Exception:
            text = ""
        out.per_url_ms[url] = (time.perf_counter() - t0) * 1000
        out.per_url_chars[url] = len(text)

    batch_started = time.perf_counter()
    await asyncio.gather(*(one(url) for url in to_scrape))
    out.scrape_parallel_ms = (time.perf_counter() - batch_started) * 1000
    return out


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--limit", type=int, default=None)
    parser.add_argument("-q", "--query", action="append", dest="queries")
    args = parser.parse_args()

    if not settings.FIRECRAWL_API_KEY:
        raise SystemExit("FIRECRAWL_API_KEY 未設定")

    queries = args.queries or load_queries(args.limit)
    client = FirecrawlClient(api_key=settings.FIRECRAWL_API_KEY)

    print(
        f"查詢 {len(queries)} 題　site filter={settings.RAG_WEB_SEARCH_SITE_FILTER}　"
        f"limit={WEB_SEARCH_LIMIT}　snippet 門檻={WEB_SNIPPET_MIN_CHARS} 字\n"
    )
    header = (
        f"{'search ms':>10} {'命中':>4} {'候選':>4} {'snippet夠':>8} "
        f"{'抓':>3} {'並行ms(實測)':>13} {'序列ms(推估)':>13}  問句"
    )
    print(header)
    print("-" * len(header))

    results: list[QueryResult] = []
    for query in queries:
        res = await measure(client, query)
        results.append(res)
        if res.error:
            print(f"{res.search_ms:10.0f} {'—':>4} {'—':>4} {'—':>8} {'—':>3} "
                  f"{'—':>13} {'—':>13}  {query[:28]}  ⚠ {res.error}")
            continue
        print(
            f"{res.search_ms:10.0f} {res.hits:4d} {res.candidates:4d} "
            f"{res.snippet_ok:8d} {res.scraped:3d} "
            f"{res.scrape_parallel_ms:13.0f} {res.scrape_sequential_ms:13.0f}  "
            f"{query[:28]}"
        )

    ok = [r for r in results if not r.error]
    if not ok:
        print("\n全部失敗，沒有可彙總的數字。")
        return

    scraped_rows = [r for r in ok if r.scraped]
    print("\n── 彙總 ──")
    print(f"成功 {len(ok)}/{len(results)} 題，其中 {len(scraped_rows)} 題需要 scrape")
    print(f"search 中位數 {median([r.search_ms for r in ok]):.0f} ms")
    total_candidates = sum(r.candidates for r in ok)
    total_snippet_ok = sum(r.snippet_ok for r in ok)
    if total_candidates:
        pct = total_snippet_ok / total_candidates * 100
        print(
            f"snippet 就夠用的候選 {total_snippet_ok}/{total_candidates}（{pct:.0f}%）"
            "　← 這個比例決定併發值不值得"
        )
    if scraped_rows:
        par = [r.scrape_parallel_ms for r in scraped_rows]
        seq = [r.scrape_sequential_ms for r in scraped_rows]
        print(f"scrape 段　並行中位數 {median(par):.0f} ms（實測）")
        print(f"scrape 段　序列中位數 {median(seq):.0f} ms（推估）")
        print(f"scrape 段　並行最差 {max(par):.0f} ms、序列最差 {max(seq):.0f} ms")
        saved = [s - p for s, p in zip(seq, par)]
        print(f"每題省下　中位數 {median(saved):.0f} ms、最多 {max(saved):.0f} ms")
    credits = len(ok) * 2 + sum(r.scraped for r in ok)
    print(f"\n這一輪用掉約 {credits} credits（search 2/題、scrape 1/次）")


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    asyncio.run(main())
