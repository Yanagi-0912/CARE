#!/usr/bin/env python3
"""產生「走失求救」分類器的訓練資料。

要分的是什麼：長輩傳來的訊息是不是在說「我此刻不知道自己在哪、找不到路」或
「想讓家人知道我現在在哪」。是的話 CARE 啟動走失求救（通知家人、分享即時位置，
見 app/services/lost/）。關鍵字版（lost_intent）只認得寫進規則的中文講法：
「誒誒我不知道我人在哪裡」多了語助詞就漏，其他五種語言一句都認不得。

為什麼用合成資料、分 bucket、多語：理由同 `scripts/build_urgency_dataset.py`。
模型也用同一支 `scripts/build_guardrail_model.py` 訓練。

這份資料的重點同樣是**困難負例**：「我媽走丟了」「如果迷路怎麼辦」「診所在哪」
「常常迷路是不是失智」「找不到健保卡」。沒有它們，模型學到的是「出現迷路、在哪
＝走失」，誤判會直接變成家人收到一則「說他走丟了」的通報。

**一般訊息的負例不生成，直接沿用。** 分類器跑在每一則訊息上，最大宗的流量是
閒聊、用藥、症狀、急症描述——guardrail 與急迫度資料集裡已經有六種語言兩萬多筆。
沿用時用多語關鍵字濾掉任何可能在講迷路或位置的列（`_MAYBE_LOST_RE`），寧可少
幾筆負例，也不要把真正的走失講法標成負例教錯模型。沿用列保留原本的 train／holdout。

用法（專案根目錄，需先 source .venv）：
  python scripts/build_lost_dataset.py --per-bucket 3 --per-bucket-other 2 --out /tmp/lost.jsonl
  python scripts/build_lost_dataset.py
  python scripts/build_guardrail_model.py --dataset evals/lost/dataset.jsonl \\
      --out resources/lost_model.json --max-miss-rate 0.01 --max-false-alarm-rate 0.002
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
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

from app.core.config import settings  # noqa: E402
from app.services.gemini import GeminiService  # noqa: E402
from scripts.build_guardrail_dataset import _normalize  # noqa: E402
from scripts.build_urgency_dataset import LANGUAGES, PRIMARY_LANGUAGE  # noqa: E402

DEFAULT_OUT = _PROJECT_ROOT / "evals" / "lost" / "dataset.jsonl"
REUSED_DATASETS = (
    _PROJECT_ROOT / "evals" / "guardrail" / "dataset.jsonl",
    _PROJECT_ROOT / "evals" / "urgency" / "dataset.jsonl",
)

LABEL_DEFINITION = (
    "傳訊息的人**本人此刻**不知道自己在哪裡、迷路、走丟、找不到回家或回去的路，"
    "需要人來找他；或是本人此刻想讓家人知道自己現在的位置、請家人來找他或接他。"
    "說的是別人走丟、假設的情況、怎麼預防走失、失智症知識、找某個地點怎麼去、"
    "找不到東西，或已經平安回到家、事情已經過去，都不算。"
)

# (bucket 名稱, label, 這個 bucket 要涵蓋什麼)
BUCKETS: tuple[tuple[str, int, str], ...] = (
    # ── 正例 ──────────────────────────────────────────────────────
    ("lost_direct", 1, "本人直接說自己迷路了、走丟了、找不到路、不知道怎麼回家、回不去"),
    ("where_am_i", 1, "本人說不知道自己現在在哪裡、認不出這是哪裡、問這裡是什麼地方，帶著慌張或求助"),
    ("describe_surroundings", 1,
     "本人不知道自己在哪，只能描述看到的東西求助：旁邊有便利商店、公車站、廟、學校、大馬路，"
     "但說不出地名或不知道怎麼回去"),
    ("stranded_transport", 1,
     "本人此刻坐錯公車、捷運或火車，下錯站、睡過站，不知道現在在哪、不知道怎麼回家"),
    ("tell_family_location", 1,
     "本人此刻想讓家人知道自己在哪：叫家人來接我、請通知我兒子女兒我在哪裡、把我的位置傳給家人，"
     "因為自己說不清楚這是哪裡"),
    ("colloquial_lost", 1,
     "以上任一種，但用長輩或語音轉文字的口吻：句首有語助詞（誒、欸、啊、嗯、那個、喂）、"
     "很短、沒有標點、有錯字、重複字、夾雜台語用字（例如「我毋知影佇佗位」「揣無路」）"),
    # ── 困難負例：出現迷路、位置、在哪，但不是本人此刻走丟 ───────────────
    ("other_person_lost", 0,
     "說話者本人沒有迷路，是家人、長輩、小孩、朋友或寵物走丟、失蹤、迷路，問怎麼辦、怎麼找、要不要報警"),
    ("hypothetical_prevention", 0,
     "假設與預防：如果迷路要怎麼辦、萬一走失、怎麼預防失智長輩走失、防走失手環或愛心手鍊怎麼申請、"
     "出門前要準備什麼"),
    ("dementia_question", 0,
     "詢問症狀或疾病知識：常常迷路是不是失智、方向感變差、記性不好認不得路是什麼原因、要看哪一科"),
    ("place_search", 0,
     "本人知道自己在哪，是在找目的地：附近的診所、藥局、廁所、醫院在哪裡，怎麼去某家醫院，"
     "公車要怎麼坐，某個科的診間在幾樓"),
    ("lost_items_figurative", 0,
     "找不到東西或比喻用法：找不到鑰匙、健保卡、藥、眼鏡；網頁或 App 找不到按鈕在哪；"
     "人生迷失方向、看不懂說明書、不知道該怎麼辦（與實際位置無關）"),
    ("location_other_purpose", 0,
     "與走失無關的位置話題：跟朋友約吃飯分享定位、叫計程車、問天氣、報平安說自己到了、"
     "問這個功能的位置分享怎麼用、問你是在哪裡開發的"),
    ("resolved_or_past", 0,
     "已經過去或已經解決：剛剛迷路但現在找到路了、已經平安到家了、上次在市場走丟後來被警察送回來、"
     "昨天坐錯車後來自己回來了"),
)

# 外語的正例與困難負例筆數（中文用 --per-bucket）。
DEFAULT_PER_BUCKET = 150
DEFAULT_PER_BUCKET_OTHER = 60

# 沿用資料集時要濾掉的列：任何語言裡可能在講迷路、走失或位置的字眼。濾掉的列不
# 標成負例也不標成正例，直接不用。寬一點沒關係，損失的只是幾筆簡單負例。
_MAYBE_LOST_RE = re.compile(
    r"迷路|走丟|走失|迷失|找不到路|回不了家|在哪|哪裡|哪裏|佗位|位置|定位|地址"
    r"|\blost\b|where am i|where i am|location|find my way|way home"
    r"|tersesat|hilang|di mana|lokasi"
    r"|lạc|ở đâu|vị trí"
    r"|หลง|อยู่ที่ไหน|ตำแหน่ง"
    r"|迷子|迷って|どこ|道に迷|場所|位置",
    re.IGNORECASE,
)

_GENERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"messages": {"type": "array", "items": {"type": "string"}}},
    "required": ["messages"],
}


def planned_cells(
    *, per_bucket: int, per_bucket_other: int
) -> list[tuple[str, str, int, str, int]]:
    """這一輪要生成的格子：(語言, bucket, label, 描述, 筆數)。"""
    return [
        (
            language,
            bucket,
            label,
            description,
            per_bucket if language == PRIMARY_LANGUAGE else per_bucket_other,
        )
        for language in LANGUAGES
        for bucket, label, description in BUCKETS
    ]


def _prompt(description: str, label: int, language: str, count: int, seed: int) -> str:
    stance = (
        f"這些訊息**應該**被判定為走失求救。走失求救的定義是：{LABEL_DEFINITION}"
        if label == 1
        else f"這些訊息**不應該**被判定為走失求救。走失求救的定義是：{LABEL_DEFINITION}"
    )
    return (
        "你在為一個台灣的 LINE 健康助理產生分類器的訓練資料。使用者多半是長輩，"
        "也有在台灣工作的外籍看護。這個分類器判斷訊息是不是本人此刻走丟、不知道自己在哪；"
        "判定為是時，助理會馬上通知家人並請他分享即時位置。\n\n"
        f"請產生 {count} 則使用者可能傳給這個助理的訊息（打字或語音轉成的文字）。\n\n"
        f"情境範圍：{description}\n\n"
        f"{stance}\n\n"
        "要求：\n"
        f"1. 全部用{LANGUAGES[language]}，口語、不要書面語、不要條列式。\n"
        "2. 長度要有變化：從三五個字到兩三句話都要有。\n"
        "3. 彼此之間差異要大——不同的地點（市場、醫院、公園、車站、巷子、山上）、"
        "語氣（慌張、平靜、不好意思）、句型。\n"
        "4. 不要編號、不要引號、不要任何前後綴，只要訊息本身。\n"
        "5. 不要出現真實人名、電話、地址或身分證字號。\n"
        f"6. 這是第 {seed} 批，請刻意避開前幾批最容易想到的講法。\n"
    )


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--per-bucket", type=int, default=DEFAULT_PER_BUCKET)
    parser.add_argument("--per-bucket-other", type=int, default=DEFAULT_PER_BUCKET_OTHER)
    parser.add_argument(
        "--batch", type=int, default=40, help="每次呼叫要幾筆。太大時模型會開始重複自己"
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--holdout-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=20260916)
    parser.add_argument("--no-reuse", action="store_true", help="不沿用既有資料集的一般負例")
    return parser.parse_args(argv)


async def _generate_batch(
    gemini: GeminiService,
    description: str,
    label: int,
    language: str,
    count: int,
    seed: int,
    sem: asyncio.Semaphore,
    tag: str,
    attempts: int = 3,
) -> list[str]:
    async with sem:
        for attempt in range(1, attempts + 1):
            try:
                result = await gemini.invoke_structured_output(
                    prompt=_prompt(description, label, language, count, seed),
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


def reused_rows(paths: tuple[Path, ...] = REUSED_DATASETS) -> list[dict[str, Any]]:
    """既有資料集裡的一般訊息，一律標 0；可能在講迷路或位置的列整列丟掉。"""
    rows: list[dict[str, Any]] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if _MAYBE_LOST_RE.search(row["text"]):
                continue
            rows.append(
                {
                    "text": row["text"],
                    "label": 0,
                    "bucket": f"reused:{path.parent.name}:{row.get('bucket', '')}",
                    "lang": row.get("lang", PRIMARY_LANGUAGE),
                    "split": row.get("split", "train"),
                    "source": f"{path.parent.name}_dataset",
                }
            )
    return rows


async def _run(args: argparse.Namespace) -> int:
    if not settings.GEMINI_API_KEY:
        print("需要 GEMINI_API_KEY", file=sys.stderr)
        return 2

    gemini = GeminiService(
        api_key=settings.GEMINI_API_KEY,
        model_name=settings.MODEL_NAME,
        # 生成資料要多樣性；1.0 也是 Gemini 3 的預設值。
        temperature=1.0,
    )
    sem = asyncio.Semaphore(args.concurrency)
    cells = planned_cells(per_bucket=args.per_bucket, per_bucket_other=args.per_bucket_other)

    tasks = []
    for language, bucket, label, description, remaining in cells:
        seed = 0
        while remaining > 0:
            seed += 1
            take = min(args.batch, remaining)
            remaining -= take
            tag = f"{language}/{bucket}"
            tasks.append(
                (bucket, label, language,
                 _generate_batch(gemini, description, label, language, take, seed, sem, tag))
            )

    print(f"共 {len(cells)} 個 (語言, bucket) 格子、{len(tasks)} 次呼叫，開始產生…")
    results = await asyncio.gather(*(t[3] for t in tasks))

    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    dropped = 0
    for (bucket, label, language, _), messages in zip(tasks, results):
        for text in messages:
            key = _normalize(text)
            if not key or key in seen:
                dropped += 1
                continue
            seen.add(key)
            rows.append({"text": text, "label": label, "bucket": bucket, "lang": language})

    # 分層切分，理由同急迫度資料集。亂數只決定切分與列序，非安全用途（NOSONAR）。
    rng = random.Random(args.seed)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["lang"], row["bucket"]), []).append(row)
    for group_rows in groups.values():
        rng.shuffle(group_rows)  # NOSONAR
        cut = int(len(group_rows) * args.holdout_ratio)
        for i, row in enumerate(group_rows):
            row["split"] = "holdout" if i < cut else "train"

    generated_at = datetime.now(timezone.utc).isoformat()
    for row in rows:
        row["source"] = "synthetic"
        row["model"] = settings.MODEL_NAME
        row["generated_at"] = generated_at

    if not args.no_reuse:
        for row in reused_rows():
            key = _normalize(row["text"])
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(row)

    rng.shuffle(rows)  # NOSONAR
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    labels = Counter(r["label"] for r in rows)
    splits = Counter(r["split"] for r in rows)
    print(f"\n寫入 {args.out}")
    print(f"  總筆數 {len(rows)}（重複／空白丟棄 {dropped}）")
    print(f"  正例 {labels[1]} / 負例 {labels[0]}")
    print(f"  train {splits['train']} / holdout {splits['holdout']}")
    print("  各語言生成筆數：")
    for language, count in sorted(Counter(r["lang"] for r in rows if r["source"] == "synthetic").items()):
        print(f"    {language:<6} {count}")
    print("  各 bucket：")
    for bucket, _, _ in BUCKETS:
        print(f"    {bucket:<26} {sum(1 for r in rows if r['bucket'] == bucket)}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    return asyncio.run(_run(_parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
