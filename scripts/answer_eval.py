#!/usr/bin/env python3
"""golden 逐題跑完整 RagAnswerService（CRAG 分級、生成、必要時網搜），記下走的路與引用來源。

跑法（專案根目錄）：python scripts/answer_eval.py --out /tmp/answer.jsonl [--concurrency 4]
- 每題跑完就追加一行到 --out；已經寫進去的 id 會跳過，斷了可以接著跑
- 網搜成功後寫知識回報的 callback 關掉，不在資料庫留下評測紀錄（同 rag_model_compare.py）
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import logging
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

# 每題各自一份 stage 紀錄：task 建立時複製 context，併發的題目不會互相寫進對方
_stages: contextvars.ContextVar[list | None] = contextvars.ContextVar("answer_eval_stages", default=None)
_STAGE_RE = re.compile(r"stage=(\w+)(.*)")


class StageCapture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        holder = _stages.get()
        if holder is None:
            return
        msg = record.getMessage()
        m = _STAGE_RE.search(msg)
        if m:
            fields = dict(re.findall(r"(\w+)=(\S+)", m.group(2)))
            holder.append({"stage": m.group(1), **fields})
        elif "rag_fail" in msg or "crag" in msg.lower():
            holder.append({"log": msg[:300]})


cap = StageCapture(level=logging.INFO)
root_logger = logging.getLogger()
root_logger.addHandler(cap)
root_logger.setLevel(logging.INFO)

from app.core.rag_sources import (  # noqa: E402
    begin_request_rag_sources,
    get_request_rag_sources,
    reset_request_rag_sources,
)
from app.dependencies import get_rag_answer_service  # noqa: E402
from app.services.rag.eval_scoring import is_refuse_ok, load_golden_jsonl  # noqa: E402

for h in root_logger.handlers:
    if h is not cap:
        h.setLevel(logging.WARNING)


async def run_one(svc, case, sem: asyncio.Semaphore, out_fh, lock: asyncio.Lock) -> None:
    async with sem:
        holder: list = []
        _stages.set(holder)
        token = begin_request_rag_sources()
        t0 = time.perf_counter()
        try:
            answer, error = await svc.answer(case.query), None
        except Exception as exc:  # noqa: BLE001 - 評測要把錯誤收進結果
            answer, error = "", f"{type(exc).__name__}: {exc}"[:300]
        ms = int((time.perf_counter() - t0) * 1000)
        sources = [{"label": s.label, "url": s.url} for s in get_request_rag_sources()]
        reset_request_rag_sources(token)
        final = next((s for s in reversed(holder) if s.get("stage") == "rag_answer"), {})
        record = {
            "id": case.id,
            "route": case.route,
            "ms": ms,
            "path": final.get("path"),
            "top_rerank": final.get("top_rerank"),
            "sources": sources,
            "refused": is_refuse_ok(answer),
            "answer": answer,
            "error": error,
            "stages": holder,
        }
        async with lock:
            out_fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            out_fh.flush()
        print(case.id, ms, record["path"], flush=True)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=str(ROOT / "evals" / "rag" / "golden.jsonl"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    svc = get_rag_answer_service()
    if svc.web_search is not None:
        svc.web_search._on_web_fallback_success = None
    out = Path(args.out)
    done = set()
    if out.exists():
        done = {json.loads(l)["id"] for l in out.read_text(encoding="utf-8").splitlines() if l.strip()}
    cases = [c for c in load_golden_jsonl(Path(args.golden)) if c.id not in done]
    print(f"to run: {len(cases)} (already done: {len(done)})", flush=True)
    sem, lock = asyncio.Semaphore(args.concurrency), asyncio.Lock()
    with out.open("a", encoding="utf-8") as fh:
        await asyncio.gather(*(run_one(svc, c, sem, fh, lock) for c in cases))


if __name__ == "__main__":
    asyncio.run(main())
