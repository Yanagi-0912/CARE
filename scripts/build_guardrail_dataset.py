#!/usr/bin/env python3
"""產生 guardrail 分類器的合成訓練資料。

要分的是什麼：`GuardrailService` 目前用 Gemini 判斷「這則訊息是否與健康醫療
或醫療識詐相關」，決定要不要把 RAG 工具掛給 agent。線上實測那一次呼叫
p50 2,036ms（最快也要 1,098ms），而它跑在每一則訊息的關鍵路徑上。本腳本
產生資料，讓那個判斷有機會換成本地分類器。

**為什麼用合成資料而不是線上流量**，兩個獨立理由：

1. **隱私。** 本專案的日誌慣例是不記使用者訊息原文（`stage=handle text_len=`
   只記長度，`claim_match` 同此）。要訓練文字分類器就得有文字，開一個專存
   原文的集合是可以做的決定，但那是產品／法遵決定，不該由「想訓模型」推動。
2. **量。** care-dev 兩天只有 11 則訊息。等真實流量累積到幾千筆要好幾年，
   而識詐那類在真實流量裡更是稀疏到近乎不存在。

**為什麼分 bucket 而不是一個大提示詞要 3000 筆**：LLM 被要求「生 500 個健康
問題」時會集中在最典型的講法，長輩口語、台語混用、錯字、識詐話術幾乎不會
出現——而那些正是分類器會出錯的地方。切成 bucket 是強迫它覆蓋。

標籤定義**逐字取自** `app/services/guardrail/service.py` 的 `_CLASSIFICATION_PROMPT`。
兩邊必須是同一個概念，否則訓出來的模型學的是另一件事。

用法（專案根目錄，需先 source .venv）：
  python scripts/build_guardrail_dataset.py --per-bucket 5      # 先試跑
  python scripts/build_guardrail_dataset.py                     # 正式產生
  python scripts/build_guardrail_dataset.py --out evals/guardrail/dataset.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

from app.core.config import settings  # noqa: E402
from app.services.gemini import GeminiService  # noqa: E402

DEFAULT_OUT = _PROJECT_ROOT / "evals" / "guardrail" / "dataset.jsonl"

# 逐字取自 guardrail/service.py 的 _CLASSIFICATION_PROMPT。
LABEL_DEFINITION = (
    "健康、醫療、身體狀況、疾病、藥物、營養、運動健身、心理健康；"
    "或醫療場景詐騙／識詐（例如假藥、假醫師、假醫院或健保相關簡訊、"
    "保證療效的可疑保健話術、因醫療／檢驗／健保／保險理賠名義要求匯款或點擊不明連結）。"
)

# (bucket 名稱, label, 這個 bucket 要涵蓋什麼)
#
# 正例與負例的 bucket 數刻意接近 1:1。真實分佈未知——線上 11 筆裡 10 筆是
# 正例，但樣本太小不足以當先驗；訓練時要調整類別權重的話，用評測集的實際
# 分佈去調，不要在生成階段就把偏差寫死。
BUCKETS: tuple[tuple[str, int, str], ...] = (
    # ── 正例 ──────────────────────────────────────────────────────
    ("common_disease", 1, "常見慢性病與症狀的照護問題（高血壓、糖尿病、感冒、失眠、關節痛）"),
    ("medication", 1, "用藥問題：劑量、服用時間、藥物交互作用、副作用、忘記吃藥怎麼辦"),
    ("nutrition_exercise", 1, "飲食營養與運動健身（該吃什麼、要運動多久、保健食品）"),
    ("mental_health", 1, "心理健康：焦慮、憂鬱、壓力、睡眠困擾、照顧者身心負擔"),
    ("elderly_colloquial", 1,
     "長輩口語的健康問題。要像真的長輩打字：省略主詞、用「金罵」「歹勢」等台語詞、"
     "沒有標點、有錯字（例如把「血壓」打成「雪壓」）"),
    ("scam_health", 1,
     "醫療場景詐騙訊息或對它的詢問：假健保局簡訊、保證根治的偏方、"
     "以理賠名義要求匯款、來路不明的藥品連結"),
    ("symptom_worry", 1, "描述身體不適並詢問嚴重性或要不要就醫（頭暈、胸悶、腳腫、傷口）"),
    ("care_context", 1, "照顧家人的健康問題：長輩用藥管理、失智照護、術後照顧、共病"),
    # ── 負例 ──────────────────────────────────────────────────────
    ("smalltalk", 0, "純閒聊與問候（你好、在嗎、謝謝、今天天氣、晚安）"),
    ("daily_task", 0, "與健康無關的日常請求：訂票、算數學、查匯率、翻譯、寫程式"),
    ("news_finance", 0, "時事、股票、房價、政治、體育賽事結果"),
    ("food_no_health", 0,
     "純粹談吃什麼、餐廳推薦、食譜作法，**不涉及**營養或健康考量"),
    ("shopping_travel", 0, "購物、旅遊、交通、住宿詢問"),
    ("bot_meta", 0, "問這個機器人本身：你是誰、你會什麼、怎麼用、可以改名字嗎"),
    ("adjacent_but_no", 0,
     "**貼著邊界但不算**的訊息：講到醫院或藥局但問的是地點交通（「長庚醫院怎麼去」）、"
     "提到保險但純粹是理財規劃、講到運動但問的是賽事比分"),
)

_GENERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "messages": {
            "type": "array",
            "items": {"type": "string"},
        }
    },
    "required": ["messages"],
}


def _prompt(bucket: str, label: int, description: str, count: int, seed: int) -> str:
    stance = (
        f"這些訊息**應該**被判定為「與下列主題相關」：{LABEL_DEFINITION}"
        if label == 1
        else f"這些訊息**不應該**被判定為相關。相關的定義是：{LABEL_DEFINITION}"
    )
    return (
        "你在為一個台灣的 LINE 健康助理產生分類器的訓練資料。\n\n"
        f"請產生 {count} 則使用者可能傳給這個助理的訊息。\n\n"
        f"主題範圍：{description}\n\n"
        f"{stance}\n\n"
        "要求：\n"
        "1. 用台灣人日常會打的繁體中文，不要書面語、不要條列式。\n"
        "2. 長度要有變化：從三五個字到兩三句話都要有。\n"
        "3. 彼此之間差異要大——不同的病名、藥名、情境、語氣、句型。\n"
        "4. 不要編號、不要引號、不要任何前後綴，只要訊息本身。\n"
        "5. 不要出現真實人名、電話、地址或身分證字號。\n"
        f"6. 這是第 {seed} 批，請刻意避開前幾批最容易想到的講法。\n"
    )


def _normalize(text: str) -> str:
    """去重用的鍵：全形轉半形、去空白與標點差異。"""
    folded = unicodedata.normalize("NFKC", text).strip().lower()
    return "".join(ch for ch in folded if not ch.isspace() and ch not in "，。！？、,.!?~～")


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--per-bucket",
        type=int,
        default=200,
        help="每個 bucket 產生幾筆（共 15 個 bucket）",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=40,
        help="每次呼叫要幾筆。太大時模型會開始重複自己",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--holdout-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=20260909)
    return parser.parse_args(argv)


async def _generate_batch(
    gemini: GeminiService,
    bucket: str,
    label: int,
    description: str,
    count: int,
    seed: int,
    sem: asyncio.Semaphore,
) -> list[str]:
    async with sem:
        try:
            result = await gemini.invoke_structured_output(
                prompt=_prompt(bucket, label, description, count, seed),
                json_schema=_GENERATION_SCHEMA,
            )
        except Exception as exc:  # 單批失敗不該讓整輪重來
            print(f"  ! {bucket} 第 {seed} 批失敗：{exc!r}", file=sys.stderr)
            return []
    messages = (result or {}).get("messages") or []
    return [str(m).strip() for m in messages if str(m).strip()]


async def _run(args: argparse.Namespace) -> int:
    if not settings.GEMINI_API_KEY:
        print("需要 GEMINI_API_KEY", file=sys.stderr)
        return 2

    gemini = GeminiService(
        api_key=settings.GEMINI_API_KEY,
        model_name=settings.MODEL_NAME,
        # 生成資料要多樣性，這裡刻意不用正式路徑的 temperature=0。
        temperature=1.0,
    )
    sem = asyncio.Semaphore(args.concurrency)

    tasks = []
    for bucket, label, description in BUCKETS:
        remaining = args.per_bucket
        seed = 0
        while remaining > 0:
            seed += 1
            take = min(args.batch, remaining)
            remaining -= take
            tasks.append(
                (
                    bucket,
                    label,
                    _generate_batch(gemini, bucket, label, description, take, seed, sem),
                )
            )

    print(f"共 {len(BUCKETS)} 個 bucket、{len(tasks)} 次呼叫，開始產生…")
    results = await asyncio.gather(*(t[2] for t in tasks))

    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    dropped = 0
    for (bucket, label, _), messages in zip(tasks, results):
        for text in messages:
            key = _normalize(text)
            if not key or key in seen:
                dropped += 1
                continue
            seen.add(key)
            rows.append({"text": text, "label": label, "bucket": bucket})

    # 分層切分：每個 bucket 各自抽 holdout，否則小 bucket 可能整個落在同一邊。
    rng = random.Random(args.seed)
    by_bucket: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_bucket.setdefault(row["bucket"], []).append(row)
    for bucket_rows in by_bucket.values():
        rng.shuffle(bucket_rows)
        cut = int(len(bucket_rows) * args.holdout_ratio)
        for i, row in enumerate(bucket_rows):
            row["split"] = "holdout" if i < cut else "train"

    rng.shuffle(rows)
    generated_at = datetime.now(timezone.utc).isoformat()
    out = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            row["source"] = "synthetic"
            row["model"] = settings.MODEL_NAME
            row["generated_at"] = generated_at
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    labels = Counter(r["label"] for r in rows)
    splits = Counter(r["split"] for r in rows)
    print(f"\n寫入 {out}")
    print(f"  總筆數 {len(rows)}（重複／空白丟棄 {dropped}）")
    print(f"  正例 {labels[1]} / 負例 {labels[0]}")
    print(f"  train {splits['train']} / holdout {splits['holdout']}")
    print("  各 bucket：")
    for bucket, _, _ in BUCKETS:
        print(f"    {bucket:<20} {len(by_bucket.get(bucket, []))}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    return asyncio.run(_run(_parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
