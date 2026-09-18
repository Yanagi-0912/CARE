#!/usr/bin/env python3
"""量測「從電視新聞標題找回原始報導」的成功率與安全門檻。

問題：長輩拍下的畫面只有標題，我們要拿它去搜出那則報導的網址。兩件事要量：

1. **找得到嗎**——這批標題有多少比例，在該台自己的新聞網站上搜得到。
2. **門檻設多少不會抄錯別則**——限定網域之後仍可能搜到同一台的別則新聞，
   `title_match_score` 要多高才只留下真正同一則的。

資料用 `evals/tv_news/golden.jsonl` 的 35 組（台別, 標題），那是 2026-09-12
從 13 台 26 支健康新聞人工標註的畫面標題，就是這個功能真正會拿到的輸入。

**會真的打 Firecrawl**：每個標題兩次搜尋（限定該台網域／不限定），預設 70 次。
`--limit` 可以先抽幾則試跑。結果存到 evals/tv_news/，判讀要人看——哪一筆是
同一則新聞沒有自動標註可以比對。

用法：
    uv run python scripts/tv_news_article_eval.py --limit 5
    uv run python scripts/tv_news_article_eval.py --out evals/tv_news/article_lookup.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.services.media.tv_news_lookup import (  # noqa: E402
    CHANNEL_DOMAINS,
    build_query,
    domain_of,
    is_channel_domain,
    title_match_score,
)
from app.services.rag.firecrawl_client import FirecrawlClient  # noqa: E402

GOLDEN = Path("evals/tv_news/golden.jsonl")


def load_cases(path: Path) -> list[tuple[str, str]]:
    seen: dict[tuple[str, str], None] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        channel, headline = row.get("channel") or "", row.get("headline") or ""
        if channel and headline:
            seen.setdefault((channel, headline), None)
    return list(seen)


async def run_one(client: FirecrawlClient, channel: str, headline: str, limit: int) -> dict:
    query = build_query(headline, channel)
    out: dict = {"channel": channel, "headline": headline, "query": query}
    for mode, domains in (
        ("channel_only", list(CHANNEL_DOMAINS.get(channel, ()))),
        ("open", None),
    ):
        if mode == "channel_only" and not domains:
            out[mode] = {"error": "unknown_channel"}
            continue
        try:
            hits = await client.search(query, limit=limit, include_domains=domains)
        except Exception as exc:  # noqa: BLE001
            out[mode] = {"error": type(exc).__name__}
            continue
        out[mode] = [
            {
                "title": hit.title,
                "url": hit.url,
                "domain": domain_of(hit.url),
                "own_site": is_channel_domain(hit.url, channel),
                "score": round(title_match_score(headline, hit.title), 3),
            }
            for hit in hits
        ]
    return out


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, default=GOLDEN)
    parser.add_argument("--limit", type=int, default=0, help="只跑前幾則（0＝全部）")
    parser.add_argument("--hits", type=int, default=5, help="每次搜尋取幾筆")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if not settings.FIRECRAWL_API_KEY:
        print("沒有 FIRECRAWL_API_KEY，無法搜尋", file=sys.stderr)
        return 1

    cases = load_cases(args.golden)
    if args.limit:
        cases = cases[: args.limit]
    print(f"{len(cases)} 組標題，每組 2 次搜尋", file=sys.stderr)

    client = FirecrawlClient(settings.FIRECRAWL_API_KEY)
    results = []
    for index, (channel, headline) in enumerate(cases, 1):
        row = await run_one(client, channel, headline, args.hits)
        results.append(row)
        best = max(
            (h["score"] for h in row.get("channel_only", []) if isinstance(h, dict)),
            default=None,
        )
        print(f"[{index}/{len(cases)}] {channel} {headline[:24]} 自家最佳={best}", file=sys.stderr)

    out = args.out or Path(
        f"evals/tv_news/article_lookup-{datetime.now(timezone.utc):%Y%m%d-%H%M}.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"寫入 {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
