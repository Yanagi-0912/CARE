#!/usr/bin/env python3
"""比較 RAG／網搜答案生成：evals/rag/golden.jsonl 逐題跑完整 RagAnswerService。

用法：python scripts/rag_model_compare.py MODEL OUT.json
MODEL 同時套到 RAG_GENERATE_MODEL_NAME 與 WEB_GENERATE_MODEL_NAME；其他照 MODEL_NAME。
網搜成功後寫知識回報的 callback 關掉，免得在資料庫留下評測紀錄。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

MODEL, OUT = sys.argv[1], Path(sys.argv[2])
os.environ["RAG_GENERATE_MODEL_NAME"] = MODEL
os.environ["WEB_GENERATE_MODEL_NAME"] = MODEL

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / ".env")


class StageCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.stages: dict[str, list] = {}

    def emit(self, record: logging.LogRecord) -> None:
        m = re.search(r"stage=(\w+) ms=(\d+)(.*)", record.getMessage())
        if m:
            self.stages.setdefault(m.group(1), []).append((int(m.group(2)), m.group(3).strip()))


cap = StageCapture()
logging.getLogger().addHandler(cap)
logging.getLogger().setLevel(logging.INFO)

from app.core.config import settings  # noqa: E402
from app.dependencies import get_rag_answer_service  # noqa: E402
from app.services.rag.eval_scoring import is_refuse_ok, load_golden_jsonl  # noqa: E402

for h in logging.getLogger().handlers:
    if h is not cap:
        h.setLevel(logging.WARNING)


async def main() -> None:
    assert settings.RAG_GENERATE_MODEL_NAME == MODEL
    svc = get_rag_answer_service()
    web = svc.web_search
    print("rag model:", svc.gemini_service.chat_model.model, "web model:", web.gemini_service.chat_model.model)
    web._on_web_fallback_success = None
    cases = load_golden_jsonl(_ROOT / "evals" / "rag" / "golden.jsonl")
    results = []
    for c in cases:
        cap.stages = {}
        t = time.perf_counter()
        try:
            ans, err = await svc.answer(c.query), None
        except Exception as exc:  # noqa: BLE001
            ans, err = "", repr(exc)[:200]
        ms = int((time.perf_counter() - t) * 1000)
        results.append({"id": c.id, "query": c.query, "route": c.route,
                        "must_not_answer": c.must_not_answer, "answer": ans, "error": err,
                        "ms": ms, "refused": is_refuse_ok(ans), "stages": cap.stages})
        print(f"{c.id} {ms}ms", flush=True)
    OUT.write_text(json.dumps({"model": MODEL, "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")


asyncio.run(main())
