#!/usr/bin/env python3
"""產生「不問 agent、直接送 RAG」分類器的訓練資料。

要分的是什麼：guardrail 放行 RAG 之後，agent 第一次呼叫 Gemini 只是在決定要用
哪個工具（正式環境 2026-09-07～17 共 144 次，中位數 3.2 秒、p90 7.2 秒；決定走
RAG 的 46 次裡，中位數 3.8 秒）。其中很大一部分最後都走 `get_rag_answer`，而且
參數就是使用者原句。本地模型有把握「這一句最後會走 RAG」時，就跳過那次呼叫。

**標籤來自正式的 agent 節點，不是 bucket 對應。** 直接呼叫
`AgentNodes.agent_node`（同一份 system prompt、同一組工具、同一套強制規則），
記下它最後發出的工具呼叫：只有 `get_rag_answer` 一個時標 1，其他標 0。bucket
對不上工具——guardrail 資料集裡的「症狀擔心」常被送去掛哪科、「照顧情境」可能
是查吃藥，用 bucket 當標籤會把這些教成 RAG。

困難負例用生成的、固定標 0，**不送 agent**：
  - 掛哪科、查吃藥紀錄、謠言查證、分享邀請、回報錯誤、問特定院所——這些有自己
    的工具，被誤送 RAG 就拿不到該有的卡片。
  - 追問（「那第二種呢」「它會有副作用嗎」）：單句送 agent 看不到前文，標出來
    會是 RAG；但線上它帶著前文，捷徑卻只看這一句、用原句查知識庫。
固定標 0 的方向是保守的：萬一 agent 其實會把某則送 RAG，損失的只是少走一次
捷徑。guardrail 資料集的負例（閒聊、購物…）也沿用成 0，同樣不送 agent。

**標籤快取**：每標一則就 append 到 `--label-cache`，重跑會跳過已標過的原句，
中斷不必重花錢。

用法（專案根目錄，需先 source .venv；會呼叫付費的 Gemini）：
  python scripts/build_rag_route_dataset.py --per-bucket 2 --per-bucket-other 0 \\
      --label-limit 20 --out /tmp/rag_route.jsonl                  # 煙霧測試
  python scripts/build_rag_route_dataset.py
  python scripts/build_guardrail_model.py --dataset evals/rag_route/dataset.jsonl \\
      --out resources/rag_route_model.json --exclude-from-thresholds everyday: ...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

from langchain_core.messages import HumanMessage  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.services.agent.utils.nodes import AgentNodes  # noqa: E402
from app.tools.claim_tools import configure_claim_tool  # noqa: E402
from app.tools.registry import get_all_tools  # noqa: E402
from app.services.gemini import GeminiService  # noqa: E402
from scripts.build_guardrail_dataset import _normalize  # noqa: E402
from scripts.build_lost_dataset import _GENERATION_SCHEMA  # noqa: E402
from scripts.build_urgency_dataset import LANGUAGES, PRIMARY_LANGUAGE  # noqa: E402

DEFAULT_OUT = _PROJECT_ROOT / "evals" / "rag_route" / "dataset.jsonl"
DEFAULT_LABEL_CACHE = _PROJECT_ROOT / "evals" / "rag_route" / "agent_labels.jsonl"
GUARDRAIL_DATASET = _PROJECT_ROOT / "evals" / "guardrail" / "dataset.jsonl"

RAG_TOOL = "get_rag_answer"

# (bucket 名稱, label, 這個 bucket 要涵蓋什麼)。全部固定標 0，理由見模組註解。
BUCKETS: tuple[tuple[str, int, str], ...] = (
    ("department_for_symptom", 0,
     "描述自己或家人的症狀，問要看哪一科、掛什麼科、該去看哪種醫生"),
    ("medication_status", 0,
     "問自己或家人（爸媽、阿公阿嬤、先生太太）今天、昨天、這禮拜有沒有吃藥、吃了幾次、"
     "早上的藥吃了沒、有沒有漏吃——要查的是這個助理記錄的服藥紀錄"),
    ("claim_check", 0,
     "轉述在 LINE 群組、電視、網路、朋友那裡聽來的健康說法，問是不是真的、有沒有根據、"
     "是不是謠言"),
    ("share_invite", 0,
     "想把這個助理分享給朋友、邀請家人一起加入使用、要加好友的連結或 QR code"),
    ("knowledge_report", 0,
     "回報這個助理剛剛的回答有錯、資料過時、連結打不開，想要反映或更正"),
    ("named_facility", 0,
     "問某一家有名字的醫院、診所、藥局的電話、地址、門診時間、今天有沒有開"),
    ("context_followup", 0,
     "接著上一輪對話的追問，只看這一句不知道在問什麼：用代名詞或省略主詞，例如"
     "「那第二種呢」「它會有副作用嗎」「那我媽可以吃嗎」「剛剛說的那個要吃多久」"
     "「如果是小孩呢」「還有別的方法嗎」"),
)

DEFAULT_PER_BUCKET = 150
DEFAULT_PER_BUCKET_OTHER = 60


def _prompt(description: str, language: str, count: int, seed: int) -> str:
    return (
        "你在為一個台灣的 LINE 健康助理產生分類器的訓練資料。使用者多半是長輩，"
        "也有在台灣工作的外籍看護。這個助理有很多功能：回答健康知識、建議掛哪一科、"
        "查家人的服藥紀錄、查證健康謠言、找院所等。分類器要找出「只是在問健康知識」的訊息，"
        "下面要你產生的是**不屬於**這一類、要交給其他功能處理的訊息。\n\n"
        f"請產生 {count} 則使用者可能傳給這個助理的訊息（打字或語音轉成的文字）。\n\n"
        f"情境範圍：{description}\n\n"
        "要求：\n"
        f"1. 全部用{LANGUAGES[language]}，口語、不要書面語、不要條列式。\n"
        "2. 長度要有變化：從三五個字到兩三句話都要有。\n"
        "3. 彼此之間差異要大——不同的疾病、藥品、家人、語氣與句型。\n"
        "4. 不要編號、不要引號、不要任何前後綴，只要訊息本身。\n"
        "5. 不要出現真實人名、電話、地址或身分證字號。\n"
        f"6. 這是第 {seed} 批，請刻意避開前幾批最容易想到的講法。\n"
    )


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--label-cache", type=Path, default=DEFAULT_LABEL_CACHE)
    parser.add_argument("--per-bucket", type=int, default=DEFAULT_PER_BUCKET)
    parser.add_argument("--per-bucket-other", type=int, default=DEFAULT_PER_BUCKET_OTHER)
    parser.add_argument("--batch", type=int, default=40)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument(
        "--label-limit", type=int, default=None, help="最多送幾則給 agent 標（煙霧測試用）"
    )
    parser.add_argument(
        "--reuse-generated",
        action="store_true",
        help=(
            "不重新生成困難負例，沿用 --out 既有檔案裡的 synthetic 列。補標上次因網路"
            "失敗而沒標到的列時用，免得生成那一段重花錢、切分也跟著變"
        ),
    )
    parser.add_argument("--holdout-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=20260917)
    return parser.parse_args(argv)


async def _generate_batch(
    gemini: GeminiService, description: str, language: str, count: int, seed: int,
    sem: asyncio.Semaphore, tag: str, attempts: int = 3,
) -> list[str]:
    async with sem:
        for attempt in range(1, attempts + 1):
            try:
                result = await gemini.invoke_structured_output(
                    prompt=_prompt(description, language, count, seed),
                    json_schema=_GENERATION_SCHEMA,
                )
            except Exception as exc:  # 單批失敗不該讓整輪重來
                print(f"  ! {tag} 第 {seed} 批第 {attempt} 次失敗：{exc!r}", file=sys.stderr)
                continue
            messages = (result or {}).get("messages") or []
            cleaned = [str(m).strip() for m in messages if str(m).strip()]
            if cleaned:
                return cleaned
        print(f"  ! {tag} 第 {seed} 批放棄", file=sys.stderr)
        return []


class _NotExecuted:
    """標註時頂替查核服務：工具只綁給模型、不會被呼叫。"""


def offer_production_tools() -> list[str]:
    """讓 agent 看到的工具清單與正式環境一致，回傳工具名稱。

    verify_claim 只有在 dependencies 注入查核服務後才會出現在清單裡，這支腳本
    不經過 dependencies——第一次標註就漏了它，agent 沒得選，重標後有 28 則從
    RAG 改判成查證。標註只把工具綁給模型看、不會執行，所以注入佔位物件就夠。
    """
    if settings.CLAIM_VERIFICATION_ENABLED:
        configure_claim_tool(_NotExecuted())
    return [t.name for t in get_all_tools(include_rag_tool=True)]


def agent_route(message: Any) -> dict[str, Any]:
    """把 agent_node 回傳的 AIMessage 攤成標籤欄位。"""
    tool_calls = getattr(message, "tool_calls", None) or []
    calls = [tc.get("name") for tc in tool_calls]
    query = None
    forced = False
    for tc in tool_calls:
        if tc.get("name") == RAG_TOOL:
            query = (tc.get("args") or {}).get("query")
            forced = str(tc.get("id", "")).startswith("forced_rag")
    return {
        "calls": calls,
        "label": 1 if calls == [RAG_TOOL] else 0,
        "rag_query": query,
        "forced_rag": forced,
    }


async def _label_one(
    nodes: AgentNodes, text: str, language: str, sem: asyncio.Semaphore, attempts: int = 3,
) -> Optional[dict[str, Any]]:
    state = {
        "messages": [HumanMessage(content=text)],
        # 捷徑只在 guardrail 放行之後才會判斷，所以標籤也在放行的前提下取。
        "allow_rag": True,
        "user_profile": {"settings": {"language": language}},
    }
    async with sem:
        for attempt in range(1, attempts + 1):
            try:
                result = await nodes.agent_node(state)
            except Exception as exc:
                print(f"  ! 標註第 {attempt} 次失敗：{exc!r}", file=sys.stderr)
                continue
            return agent_route(result["messages"][0])
    return None


def _load_label_cache(path: Path) -> dict[str, dict[str, Any]]:
    cache: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return cache
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            cache[row["text"]] = row
    return cache


async def _label_rows(
    rows: list[dict[str, Any]], args: argparse.Namespace
) -> dict[str, dict[str, Any]]:
    cache = _load_label_cache(args.label_cache)
    todo = [r for r in rows if r["text"] not in cache]
    if args.label_limit is not None:
        todo = todo[: args.label_limit]
    print(f"agent 標註：快取 {len(cache)} 則，這次要標 {len(todo)} 則")
    if not todo:
        return cache

    # 與 dependencies 裡 agent 用的實例同一種建法：正式模型、不設 temperature。
    gemini = GeminiService(api_key=settings.GEMINI_API_KEY, model_name=settings.MODEL_NAME)
    nodes = AgentNodes(llm=gemini.chat_model, guardrail_service=None)
    offered = offer_production_tools()
    print(f"標註時提供給 agent 的工具（{len(offered)} 個）：{offered}")
    sem = asyncio.Semaphore(args.concurrency)
    args.label_cache.parent.mkdir(parents=True, exist_ok=True)

    done = 0
    with args.label_cache.open("a", encoding="utf-8") as fh:

        async def run(row: dict[str, Any]) -> None:
            nonlocal done
            route = await _label_one(nodes, row["text"], row["lang"], sem)
            if route is None:
                return
            entry = {
                "text": row["text"],
                "lang": row["lang"],
                **route,
                "model": settings.MODEL_NAME,
                "labeled_at": datetime.now(timezone.utc).isoformat(),
            }
            cache[row["text"]] = entry
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            fh.flush()
            done += 1
            if done % 200 == 0:
                print(f"  已標 {done}/{len(todo)}")

        await asyncio.gather(*(run(r) for r in todo))
    return cache


def _guardrail_rows() -> list[dict[str, Any]]:
    rows = []
    for line in GUARDRAIL_DATASET.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows.append(
            {
                "text": row["text"],
                "guardrail_label": int(row["label"]),
                # 非健康訊息另給前綴：它們只進訓練、不進門檻選擇（線上 guardrail
                # 已經擋掉，算進誤判率的分母只會把門檻壓低）。
                "bucket": (
                    f"reused:guardrail:{row.get('bucket', '')}"
                    if int(row["label"]) == 1
                    else f"everyday:guardrail:{row.get('bucket', '')}"
                ),
                "lang": row.get("lang", PRIMARY_LANGUAGE),
                "split": row.get("split", "train"),
                "source": "guardrail_dataset",
            }
        )
    return rows


async def _generate(args: argparse.Namespace) -> tuple[list[dict[str, Any]], set[str]]:
    generator = GeminiService(
        api_key=settings.GEMINI_API_KEY, model_name=settings.MODEL_NAME, temperature=1.0
    )
    sem = asyncio.Semaphore(4)
    cells = [
        (language, bucket, description,
         args.per_bucket if language == PRIMARY_LANGUAGE else args.per_bucket_other)
        for language in LANGUAGES
        for bucket, _label, description in BUCKETS
    ]
    tasks = []
    for language, bucket, description, remaining in cells:
        seed = 0
        while remaining > 0:
            seed += 1
            take = min(args.batch, remaining)
            remaining -= take
            tasks.append(
                (bucket, language,
                 _generate_batch(generator, description, language, take, seed, sem,
                                 f"{language}/{bucket}"))
            )
    print(f"生成困難負例：{len(tasks)} 次呼叫")
    results = await asyncio.gather(*(t[2] for t in tasks))

    seen: set[str] = set()
    generated: list[dict[str, Any]] = []
    for (bucket, language, _), messages in zip(tasks, results):
        for text in messages:
            key = _normalize(text)
            if not key or key in seen:
                continue
            seen.add(key)
            generated.append({"text": text, "label": 0, "bucket": bucket, "lang": language,
                              "source": "synthetic", "model": settings.MODEL_NAME})

    rng = random.Random(args.seed)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in generated:
        groups.setdefault((row["lang"], row["bucket"]), []).append(row)
    for group_rows in groups.values():
        rng.shuffle(group_rows)  # NOSONAR：只決定切分，非安全用途
        cut = int(len(group_rows) * args.holdout_ratio)
        for i, row in enumerate(group_rows):
            row["split"] = "holdout" if i < cut else "train"

    return generated, seen


async def _run(args: argparse.Namespace) -> int:
    if not settings.GEMINI_API_KEY:
        print("需要 GEMINI_API_KEY", file=sys.stderr)
        return 2
    # agent_node 每則都記 stage=agent_decide，幾千行會淹掉進度。
    logging.basicConfig(level=logging.WARNING)

    if args.reuse_generated:
        generated = [
            row
            for row in (
                json.loads(line)
                for line in args.out.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
            if row.get("source") == "synthetic"
        ]
        seen = {_normalize(row["text"]) for row in generated}
        print(f"沿用 {args.out} 的困難負例 {len(generated)} 筆")
    else:
        generated, seen = await _generate(args)

    reused = []
    for row in _guardrail_rows():
        key = _normalize(row["text"])
        if not key or key in seen:
            continue
        seen.add(key)
        reused.append(row)

    to_label = [r for r in reused if r["guardrail_label"] == 1]
    labels = await _label_rows(to_label, args)

    rows: list[dict[str, Any]] = list(generated)
    unlabeled = 0
    for row in reused:
        guardrail_label = row.pop("guardrail_label")
        if guardrail_label == 0:
            row["label"] = 0
        elif row["text"] in labels:
            entry = labels[row["text"]]
            row["label"] = entry["label"]
            row["agent_calls"] = entry["calls"]
        else:
            unlabeled += 1
            continue
        rows.append(row)

    random.Random(args.seed).shuffle(rows)  # NOSONAR：只決定列序
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"\n寫入 {args.out}：{len(rows)} 筆（健康相關但沒標到而略過 {unlabeled}）")
    print(f"  正例 {sum(r['label'] for r in rows)} / 負例 {sum(1 - r['label'] for r in rows)}")
    agent_rows = [labels[r["text"]] for r in to_label if r["text"] in labels]
    if agent_rows:
        calls = Counter(tuple(e["calls"]) for e in agent_rows)
        print("  agent 對健康相關訊息的決定：")
        for key, count in calls.most_common():
            print(f"    {list(key)!s:<60} {count}")
        rag = [e for e in agent_rows if e["label"] == 1]
        same = sum(1 for e in rag if (e["rag_query"] or "").strip() == e["text"].strip())
        forced = sum(1 for e in rag if e["forced_rag"])
        print(f"  走 RAG 的 {len(rag)} 則：query 與原句相同 {same}、強制轉 RAG {forced}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    return asyncio.run(_run(_parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
