#!/usr/bin/env python3
"""比較 agent 選工具：同一批訊息各用一個模型跑正式的 agent_node，記工具與耗時。

用法：python scripts/agent_model_compare.py MODEL OUT.jsonl
取樣固定（seed 0）：非 RAG 的標籤全取，RAG 每語言取 40 則。
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_ROOT / ".env")

import logging  # noqa: E402

logging.disable(logging.INFO)

from app.core.config import settings  # noqa: E402
from app.services.agent.utils.nodes import AgentNodes  # noqa: E402
from app.services.gemini.services.gemini_service import GeminiService  # noqa: E402
from build_rag_route_dataset import agent_route, offer_production_tools  # noqa: E402
from langchain_core.messages import HumanMessage  # noqa: E402

LABELS = _ROOT / "evals" / "rag_route" / "agent_labels.jsonl"


def sample() -> list[dict]:
    rows = [json.loads(x) for x in LABELS.read_text(encoding="utf-8").splitlines() if x.strip()]
    rng = random.Random(0)
    other = [r for r in rows if r["calls"] != ["get_rag_answer"]]
    rag_by_lang = defaultdict(list)
    for r in rows:
        if r["calls"] == ["get_rag_answer"]:
            rag_by_lang[r["lang"]].append(r)
    picked = list(other)
    for lang in sorted(rag_by_lang):
        picked += rng.sample(rag_by_lang[lang], min(40, len(rag_by_lang[lang])))
    return picked


async def main(model: str, out: Path) -> None:
    gemini = GeminiService(api_key=settings.GEMINI_API_KEY, model_name=model)
    nodes = AgentNodes(llm=gemini.chat_model, guardrail_service=None)
    offer_production_tools()
    rows = sample()
    sem = asyncio.Semaphore(4)
    results = []

    async def run(r: dict) -> None:
        state = {
            "messages": [HumanMessage(content=r["text"])],
            "allow_rag": True,
            "user_profile": {"settings": {"language": r["lang"]}},
        }
        async with sem:
            t = time.perf_counter()
            try:
                res = await nodes.agent_node(state)
                route = agent_route(res["messages"][0])
                err = None
            except Exception as exc:  # noqa: BLE001
                route, err = {"calls": None, "rag_query": None}, repr(exc)[:200]
            ms = int((time.perf_counter() - t) * 1000)
        results.append({"text": r["text"], "lang": r["lang"], "label_calls": r["calls"],
                        "label_query": r.get("rag_query"), **route, "ms": ms, "error": err})

    await asyncio.gather(*(run(r) for r in rows))
    out.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in results) + "\n", encoding="utf-8")
    print(f"{model}: {len(results)} rows -> {out}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], Path(sys.argv[2])))
