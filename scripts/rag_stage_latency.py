"""跑真實的 RAG 管線，把每一段的耗時（stage=… ms=…）攤出來。

跑法：
    .venv/bin/python scripts/rag_stage_latency.py           # golden set 的 web 題
    .venv/bin/python scripts/rag_stage_latency.py -n 5
    .venv/bin/python scripts/rag_stage_latency.py -q "問句"

**這會打真實的 Gemini、Cohere、Firecrawl、MongoDB Atlas，全部算額度。**
一題會用到：向量檢索 1 次 embedding、Cohere rerank 1 次、Gemini 2~3 次
（CRAG 分級、查詢改寫、生成），走網搜的話再加 Firecrawl search 1~2 次。

刻意不設 line_user_id：網搜成功會觸發知識回報自動建報，設了就會把測試跑的
問題灌進待審佇列。少了它那一段只會記一行 skip，不影響計時。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

GOLDEN = Path(__file__).resolve().parents[1] / "evals" / "rag" / "golden.jsonl"
STAGE_RE = re.compile(r"stage=(?P<stage>\S+)(?:\s+.*?\bms=(?P<ms>\d+))?")


class StageCollector(logging.Handler):
    """把 `stage=… ms=…` 那幾行攔下來，其餘照舊。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.rows: list[tuple[str, int, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:
            return
        if not message.startswith("stage="):
            return
        match = STAGE_RE.match(message)
        if not match or match.group("ms") is None:
            return
        self.rows.append(
            (match.group("stage"), int(match.group("ms")), message)
        )

    def take(self) -> list[tuple[str, int, str]]:
        rows, self.rows = self.rows, []
        return rows


def load_queries(limit: int | None, route: str = "web") -> list[str]:
    rows = [json.loads(line) for line in GOLDEN.read_text().splitlines() if line.strip()]
    queries = [r["query"] for r in rows if route == "all" or r.get("route") == route]
    return queries[:limit] if limit else queries


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-n", "--limit", type=int, default=5, help="0＝全部")
    parser.add_argument("--route", choices=("web", "kb", "all"), default="web")
    parser.add_argument("-q", "--query", action="append", dest="queries")
    parser.add_argument(
        "--verbose", action="store_true", help="每題印出完整的 stage 行"
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="每題的路徑、各段耗時與答案全文寫成 JSON"
    )
    args = parser.parse_args()

    collector = StageCollector()
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger().addHandler(collector)
    logging.getLogger("app").setLevel(logging.INFO)
    logging.getLogger("app").propagate = True

    from app import dependencies  # noqa: E402  匯入即建好整組服務

    service = dependencies._rag_answer_service
    if service is None:
        raise SystemExit("RAG 服務未初始化（檢查 .env 的 MONGODB_URI／GEMINI_API_KEY）")

    queries = args.queries or load_queries(args.limit or None, args.route)
    print(f"跑 {len(queries)} 題　web_fallback={service.web_fallback_enabled}\n")

    per_stage: dict[str, list[int]] = defaultdict(list)
    records: list[dict] = []
    totals: list[float] = []
    paths: list[str] = []

    for query in queries:
        collector.take()  # 清掉上一題的殘留
        started = time.perf_counter()
        try:
            answer = await service.answer(query)
        except Exception as exc:
            answer = f"<例外 {type(exc).__name__}: {exc}>"
        wall = (time.perf_counter() - started) * 1000
        totals.append(wall)

        rows = collector.take()
        path = "?"
        for stage, ms, message in rows:
            per_stage[stage].append(ms)
            if stage == "rag_answer":
                found = re.search(r"\bpath=(\S+)", message)
                if found:
                    path = found.group(1)
        paths.append(path)
        records.append(
            {
                "query": query,
                "total_ms": round(wall),
                "path": path,
                "rag_fail": (re.match(r"\[RAG_ERR:(\w+)\]", answer) or [None, None])[1],
                "citations": len(set(re.findall(r"\[(\d+)\]", answer))),
                "answer_chars": len(answer),
                "stages": [message for _stage, _ms, message in rows],
                "answer": answer,
            }
        )

        print(f"── {query[:34]}　總計 {wall:.0f} ms　path={path}")
        for stage, ms, message in sorted(rows, key=lambda r: -r[1]):
            share = ms / wall * 100 if wall else 0
            detail = message if args.verbose else f"stage={stage}"
            print(f"     {ms:7d} ms  {share:5.1f}%  {detail}")
        print(f"     答案：{answer[:70]}...\n")

    print("── 各段彙總（中位數 ms）──")
    ordered = sorted(per_stage.items(), key=lambda kv: -median(kv[1]))
    for stage, values in ordered:
        print(
            f"  {median(values):7.0f} ms  n={len(values):2d}  "
            f"最大 {max(values):6d}  {stage}"
        )
    print(f"\n總耗時中位數 {median(totals):.0f} ms、最大 {max(totals):.0f} ms")
    print("path 分佈：", {p: paths.count(p) for p in sorted(set(paths))})
    if args.out:
        args.out.write_text(json.dumps(records, ensure_ascii=False, indent=2))
        print(f"wrote {args.out}")


def median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


if __name__ == "__main__":
    asyncio.run(main())
