#!/usr/bin/env python3
"""產生急迫度分類器的合成訓練資料。

要分的是什麼：`UrgencyClassifier`（app/services/medical/symptom_classification/urgency.py）
用 Gemini 判斷「這則訊息描述的狀況是否正在發生、且需要立即急診處置」，決定要不要
略過 agent 直接回紅卡並通報家人。那一次呼叫跑在每一則訊息的關鍵路徑上。本腳本
產生資料，讓大部分訊息改由本地分類器判定，只有沒把握的才問 Gemini——串接方式
與 guardrail 相同（app/services/guardrail/cascade.py），模型也用同一支
`scripts/build_guardrail_model.py` 訓練。

為什麼用合成資料、為什麼分 bucket：理由與 `scripts/build_guardrail_dataset.py`
相同（隱私、量、強迫 LLM 覆蓋非典型講法）。這裡多兩件事：

1. **困難負例是這份資料的重點。** 關鍵字版被打穿的地方正是「同一個詞，不同的
   處境」：「我阿公昏迷」與「昏迷的原因有哪些」、「我想死」與「這題難到我想死」。
   沒有這些負例，分類器學到的就是「出現急症詞＝緊急」，退化回關鍵字版。
2. **多語。** 使用者語言有六種（app/core/user_language.py），關鍵字版只有中文，
   其他語言完全沒有安全網。每種語言都產生正負例；字元 n-gram 不需要斷詞，
   泰文、日文可以直接用。

另外沿用 guardrail 資料集裡**明顯不可能是急症**的 bucket 當簡單負例（閒聊、
日常請求、用藥與慢性病問題等），那是真實流量的大宗。中文直接沿用現成的列；
guardrail 資料集只有中文，外語就用同一份 bucket 定義生成（`EVERYDAY_BUCKETS`）。
刻意排除 `symptom_worry`、`mental_health`、`care_context`、`elderly_colloquial`
——那幾個 bucket 可能含有正在發生的急症或自傷意念，當負例會教錯。

標籤定義摘自 urgency.py 的 `_PROMPT_TEMPLATE` 判準。兩邊必須是同一個概念，
否則訓出來的模型學的是另一件事。

用法（專案根目錄，需先 source .venv）：
  python scripts/build_urgency_dataset.py --per-bucket 5 --per-bucket-other 2 --out /tmp/u.jsonl
  python scripts/build_urgency_dataset.py
  python scripts/build_urgency_dataset.py --fill-missing   # 每格補到目標筆數，既有的列不動
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from collections import Counter
from collections.abc import Mapping
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
from scripts.build_guardrail_dataset import BUCKETS as GUARDRAIL_BUCKETS  # noqa: E402
from scripts.build_guardrail_dataset import _normalize  # noqa: E402

DEFAULT_OUT = _PROJECT_ROOT / "evals" / "urgency" / "dataset.jsonl"
GUARDRAIL_DATASET = _PROJECT_ROOT / "evals" / "guardrail" / "dataset.jsonl"

# 摘自 urgency.py `_PROMPT_TEMPLATE` 的判準：兩個條件同時成立才算緊急，
# 自傷意念屬於範圍內，誇飾與談論作品不算。
LABEL_DEFINITION = (
    "使用者或他身邊的人**此刻**正處於這個狀況（正在發生或剛剛發生；不是詢問知識、"
    "成因、預防、保險或理賠，也不是已經過去、正在做復健回診等後續照護的事），"
    "**而且**這個狀況需要立刻叫救護車或前往急診、不適合等一般門診掛號"
    "（例如意識不清、叫不醒、大量出血、呼吸困難、抽搐、疑似中風的徵象、"
    "劇烈胸痛、重大外傷、中毒、嚴重燒燙傷）。"
    "表達想結束生命、想傷害自己，或已經發生的自傷行為，也屬於緊急；"
    "但把「想死」「自盡」當誇飾，或在談論一部作品時，不算。"
)

# (bucket 名稱, label, 這個 bucket 要涵蓋什麼)
BUCKETS: tuple[tuple[str, int, str], ...] = (
    # ── 正例 ──────────────────────────────────────────────────────
    ("unconscious_collapse", 1, "有人此刻失去意識、叫不醒、突然倒地、反應突然變得很遲鈍"),
    ("breathing_chest", 1, "此刻呼吸困難、喘不過氣、劇烈胸痛或胸口壓迫並冒冷汗、嘴唇發紫"),
    ("stroke_signs", 1, "此刻出現疑似中風的徵象：嘴歪、單側手腳無力或發麻、講話突然不清楚、突然看不見"),
    ("trauma_bleeding", 1, "剛發生的車禍、跌倒撞到頭、被割傷刺傷，血流不止、重大外傷、骨頭變形"),
    ("poisoning_overdose", 1, "剛誤食或吞下有毒物質：農藥、清潔劑、一整罐藥、一氧化碳中毒、被毒蛇咬"),
    ("other_acute", 1, "此刻正在抽搐、嚴重燒燙傷、嚴重過敏喉嚨腫起來、高燒並意識混亂、溺水"),
    ("self_harm", 1,
     "此刻表達想結束生命或傷害自己，或剛做了自傷行為：想跳樓、想燒炭、不想活了、割腕、吞藥"),
    # 第一版資料訓出來的模型，交叉驗證裡機率最低的正例是「受夠這個爛世界了，這是
    # 我最後一則訊息，謝謝你，再見了」——沒有任何急症詞，而它一個就把本地放行
    # 門檻壓到 0.076。這類訊息不能靠放寬門檻處理，只能補資料。
    ("indirect_self_harm", 1,
     "沒有直接說自殺、但此刻透露要結束生命的訊息：道別、交代後事、說這是最後一則訊息、"
     "說再也不會麻煩大家、把東西送人、說終於可以解脫"),
    ("colloquial_emergency", 1,
     "以上任一種正在發生的急症，但用長輩或慌張家屬的口吻：省略主詞、很短、沒有標點、"
     "有錯字、夾雜台語（例如「阿公叫袂醒」）"),
    # ── 困難負例：出現急症詞，但不是此刻需要急診 ─────────────────────
    ("knowledge_about_emergency", 0,
     "詢問急症的知識：成因、前兆、症狀、預防、怎麼急救、要注意什麼。提問者此刻並沒有處於那個狀況"),
    ("past_followup", 0,
     "已經過去、正在做後續照護的急症：中風後復健、車禍後回診、上個月住院、開刀後追蹤、要掛哪一科"),
    ("non_urgent_symptom", 0,
     "正在發生但不需要叫救護車的一般不適：輕微頭痛、感冒、咳嗽、拉肚子、小擦傷、皮膚癢、想知道掛哪一科"),
    ("figurative_self_harm", 0,
     "把「想死」「自殺」「累死」「笑死」「快昏倒」當誇飾或抱怨：工作太累、考試太難、天氣太熱、"
     "被老闆氣到，並不是真的想傷害自己"),
    ("third_party_story", 0,
     "談論新聞、電視劇、電影、紀錄片、小說，或別人很久以前的經歷裡出現的急症、意外或自殺；"
     "說話者身邊此刻沒有人處於那個狀況"),
    ("admin_about_emergency", 0,
     "與急症相關的行政問題：急診怎麼收費、救護車要不要錢、車禍保險理賠、診斷證明怎麼申請、急救課程報名"),
    # indirect_self_harm 的對照組：沒有它，模型會把「謝謝、再見」本身學成緊急訊號。
    ("ordinary_farewell", 0,
     "一般的道別、道謝與結束對話：先去忙了、謝謝你的幫忙、晚安再見、明天再聊、"
     "出國前跟大家說再見、離職感謝信，與傷害自己無關"),
)

# 語言代碼與 SUPPORTED_LANGUAGES 一致。非中文的描述刻意帶到「在台灣的外籍看護」：
# 那是這幾種語言在本專案裡最主要的使用者，他們描述的多半是照顧對象的狀況。
LANGUAGES: dict[str, str] = {
    "zh-TW": "台灣人日常會打的繁體中文",
    "en": "英文（在台灣生活的外國人或外籍看護會打的英文）",
    "id": "印尼文（在台灣工作的印尼籍看護會打的印尼文）",
    "vi": "越南文（在台灣工作的越南籍看護或新住民會打的越南文）",
    "th": "泰文（在台灣工作的泰籍移工會打的泰文）",
    "ja": "日文（在台灣生活的日本人會打的日文）",
}
PRIMARY_LANGUAGE = "zh-TW"

# guardrail 資料集裡可以直接當「不緊急」的 bucket，理由見模組註解。
REUSED_GUARDRAIL_BUCKETS: frozenset[str] = frozenset(
    {
        "common_disease",
        "medication",
        "nutrition_exercise",
        "scam_health",
        "smalltalk",
        "daily_task",
        "news_finance",
        "food_no_health",
        "shopping_travel",
        "bot_meta",
        "adjacent_but_no",
    }
)

# 外語的日常訊息。中文沿用 guardrail 資料集的現成列；外語沒有現成的，就用同一份
# bucket 定義生成，每格筆數同 --per-bucket-other。bucket 名稱沿用 `guardrail:`
# 前綴，同一類訊息才能跨語言比放行率（來源看 `source` 欄）。
#
# 為什麼要補：少了這幾類，外語資料只有急症與困難負例，模型沒看過外語的日常訊息
# ——在台灣的外籍看護實際傳的多半是這種——holdout 也量不到它們的放行率。
#
# 標籤一律 0：guardrail 把用藥、慢性病問題標成 1（是醫療問題），但它們不是急症。
EVERYDAY_BUCKETS: tuple[tuple[str, int, str], ...] = tuple(
    (f"guardrail:{name}", 0, description)
    for name, _, description in GUARDRAIL_BUCKETS
    if name in REUSED_GUARDRAIL_BUCKETS
)

# 這兩類在每種語言都至少生成這麼多筆（與中文的 --per-bucket 預設相同）。
# 本地放行門檻 low 以「交叉驗證零漏判」選定，等於 train 裡機率最低的那則急症，而那則
# 急症幾乎都落在自傷兩類，尤其是沒有急症詞的隱晦道別。加入外語日常訊息後，交叉驗證
# 機率最低的 20 則急症有 18 則屬於這兩類、其中 15 則是外語：外語每類只有 60 筆時，
# 模型分不清「謝謝、再見」是客套還是訣別，low 從 0.106 被壓到 0.030。
MIN_PER_BUCKET: dict[str, int] = {"self_harm": 150, "indirect_self_harm": 150}

_GENERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"messages": {"type": "array", "items": {"type": "string"}}},
    "required": ["messages"],
}


def planned_cells(
    existing: Mapping[tuple[str, str], int],
    *,
    per_bucket: int,
    per_bucket_other: int,
) -> list[tuple[str, str, int, str, int]]:
    """這一輪要生成的格子：(語言, bucket, label, 描述, 還要生成幾筆)。

    `existing` 是資料集裡每格已有的筆數，已達目標的格子不列。
    """
    cells: list[tuple[str, str, int, str, int]] = []
    for language in LANGUAGES:
        is_primary = language == PRIMARY_LANGUAGE
        buckets = BUCKETS if is_primary else BUCKETS + EVERYDAY_BUCKETS
        for bucket, label, description in buckets:
            default = per_bucket if is_primary else per_bucket_other
            target = max(default, MIN_PER_BUCKET.get(bucket, 0))
            remaining = target - existing.get((language, bucket), 0)
            if remaining > 0:
                cells.append((language, bucket, label, description, remaining))
    return cells


def _prompt(description: str, label: int, language: str, count: int, seed: int) -> str:
    stance = (
        f"這些訊息**應該**被判定為緊急。緊急的定義是：{LABEL_DEFINITION}"
        if label == 1
        else f"這些訊息**不應該**被判定為緊急。緊急的定義是：{LABEL_DEFINITION}"
    )
    return (
        "你在為一個台灣的 LINE 健康助理產生安全分類器的訓練資料。這個分類器判斷"
        "使用者訊息是否描述一個正在發生的緊急狀況；判定為緊急時，助理會提供 119 "
        "撥號並通知家人。\n\n"
        f"請產生 {count} 則使用者可能傳給這個助理的訊息。\n\n"
        f"情境範圍：{description}\n\n"
        f"{stance}\n\n"
        "要求：\n"
        f"1. 全部用{LANGUAGES[language]}，口語、不要書面語、不要條列式。\n"
        "2. 長度要有變化：從三五個字到兩三句話都要有。\n"
        "3. 彼此之間差異要大——不同的當事人（本人、長輩、配偶、小孩、照顧對象）、"
        "情境、語氣、句型。\n"
        "4. 不要編號、不要引號、不要任何前後綴，只要訊息本身。\n"
        "5. 不要出現真實人名、電話、地址或身分證字號。\n"
        f"6. 這是第 {seed} 批，請刻意避開前幾批最容易想到的講法。\n"
    )


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--per-bucket", type=int, default=150, help="繁體中文每個 bucket 產生幾筆"
    )
    # 其他語言取 60 而不是 30：30 筆時外語負例在 holdout 只有 6–11% 能由本地放行，
    # 並出現「笑到快死了」判成高信心緊急的誤判——那幾種語言的樣本不夠模型校準。
    parser.add_argument(
        "--per-bucket-other", type=int, default=60, help="其他語言每個 bucket 產生幾筆"
    )
    parser.add_argument(
        "--batch", type=int, default=40, help="每次呼叫要幾筆。太大時模型會開始重複自己"
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--holdout-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=20260914)
    parser.add_argument(
        "--no-reuse-guardrail",
        action="store_true",
        help="不沿用 guardrail 資料集的簡單負例",
    )
    parser.add_argument(
        "--fill-missing",
        action="store_true",
        help="每格只補到目標筆數（--out 裡已有的算進去）；既有的列與 train／holdout 切分不動",
    )
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
    # 要重試：自傷類的批次偶爾會回空的 JSON（實測泰文自傷 5 批中 2 批，推測是
    # 安全過濾）。其他語言每個 bucket 只有一批，不重試就會整格缺掉——而缺的
    # 正好是最不能缺的那一格。
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


def _read_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _reused_guardrail_rows() -> list[dict[str, Any]]:
    """guardrail 資料集裡的簡單負例。沿用它原本的 split，holdout 仍是沒看過的資料。"""
    rows: list[dict[str, Any]] = []
    for line in GUARDRAIL_DATASET.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("bucket") not in REUSED_GUARDRAIL_BUCKETS:
            continue
        rows.append(
            {
                "text": row["text"],
                "label": 0,
                "bucket": f"guardrail:{row['bucket']}",
                "lang": PRIMARY_LANGUAGE,
                "split": row.get("split", "train"),
                "source": "guardrail_dataset",
                "model": row.get("model"),
                "generated_at": row.get("generated_at"),
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
        # 生成資料要多樣性，這裡刻意不用正式路徑的 temperature=0。
        temperature=1.0,
    )
    sem = asyncio.Semaphore(args.concurrency)

    # 補格子模式：既有的列原封不動（包含 split——重切會讓 holdout 混進訓練過的
    # 資料），每格只補到目標筆數。
    existing_rows = _read_rows(args.out) if args.fill_missing and args.out.exists() else []
    cells = planned_cells(
        Counter((row["lang"], row["bucket"]) for row in existing_rows),
        per_bucket=args.per_bucket,
        per_bucket_other=args.per_bucket_other,
    )

    tasks = []
    for language, bucket, label, description, remaining in cells:
        seed = 0
        while remaining > 0:
            seed += 1
            take = min(args.batch, remaining)
            remaining -= take
            tag = f"{language}/{bucket}"
            tasks.append(
                (
                    bucket,
                    label,
                    language,
                    _generate_batch(
                        gemini, description, label, language, take, seed, sem, tag
                    ),
                )
            )

    print(f"共 {len(cells)} 個 (語言, bucket) 格子、{len(tasks)} 次呼叫，開始產生…")
    results = await asyncio.gather(*(t[3] for t in tasks))

    # 既有的列也要進去重集合：新生成的句子不能跟它們重複，沿用的 guardrail 列也
    # 不能再加一次。
    seen: set[str] = {_normalize(row["text"]) for row in existing_rows}
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

    # 分層切分：每個 (語言, bucket) 各自抽 holdout，否則小語言的 bucket 可能整個
    # 落在同一邊，holdout 就量不到那個語言。
    #
    # 這裡的亂數只決定哪些樣本進 holdout、檔案內的列序，與任何安全性無關；而且
    # 必須能以 --seed 重現，不能換成 secrets。故對 SonarCloud S2245 標 NOSONAR。
    rng = random.Random(args.seed)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault((row["lang"], row["bucket"]), []).append(row)
    for group_rows in groups.values():
        rng.shuffle(group_rows)  # NOSONAR：資料切分，非安全用途（見上）
        cut = int(len(group_rows) * args.holdout_ratio)
        for i, row in enumerate(group_rows):
            row["split"] = "holdout" if i < cut else "train"

    generated_at = datetime.now(timezone.utc).isoformat()
    for row in rows:
        row["source"] = "synthetic"
        row["model"] = settings.MODEL_NAME
        row["generated_at"] = generated_at

    if not args.no_reuse_guardrail:
        for row in _reused_guardrail_rows():
            key = _normalize(row["text"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)

    rng.shuffle(rows)  # NOSONAR：列序打散，非安全用途（見切分處註解）
    # 既有的列照原順序放前面、新的接在後面：檔案的 diff 只有新增的列，審得出這次補了什麼。
    rows = existing_rows + rows
    out = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    labels = Counter(r["label"] for r in rows)
    splits = Counter(r["split"] for r in rows)
    print(f"\n寫入 {out}")
    print(f"  總筆數 {len(rows)}（重複／空白丟棄 {dropped}）")
    print(f"  正例 {labels[1]} / 負例 {labels[0]}")
    print(f"  train {splits['train']} / holdout {splits['holdout']}")
    print("  各語言：")
    for language, count in sorted(Counter(r["lang"] for r in rows).items()):
        print(f"    {language:<6} {count}")
    print("  各 bucket（生成的部分）：")
    for bucket, _, _ in BUCKETS:
        print(f"    {bucket:<28} {sum(1 for r in rows if r['bucket'] == bucket)}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    return asyncio.run(_run(_parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
