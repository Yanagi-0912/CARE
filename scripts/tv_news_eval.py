#!/usr/bin/env python3
"""量測視覺模型在「長輩拍電視新聞畫面」上的兩件事：認不認得台、讀不讀得出主標題。

  python scripts/tv_news_eval.py --golden evals/tv_news/golden.jsonl --out /tmp/tv.json

golden.jsonl 每行一張圖：
  {"image": "相對 CARE 根目錄的路徑", "variant": "clean", "channel": "三立",
   "headline": "主標題逐字", "logo_visible": true}
headline 為 null 代表未標註（只量台別）；空字串代表畫面上確實沒有主標題。
logo_visible 為 false 代表畫面上根本沒有台標／台名——這種畫面回「不確定」
才是對的，所以台別指標另外分開算，不混進主要數字。
variant 用來分組比較（例如 YouTube 原始截圖 vs 模擬手機翻拍）。

台別指標把「答錯」跟「不確定」分開算：回不確定是可接受的棄權，答錯才是傷害——
台別講錯，長輩會連帶不信整張判定卡。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional, Sequence

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

from app.core.config import settings
from app.services.gemini import GeminiService
from app.services.gemini.shared.errors import GeminiError
from scripts.handwriting_eval import _mime_for, cer

DEFAULT_GOLDEN = _PROJECT_ROOT / "evals" / "tv_news" / "golden.jsonl"

CHANNELS = [
    "台視", "中視", "華視", "民視", "公視", "TVBS", "三立",
    "東森", "中天", "年代", "壹電視", "非凡", "寰宇",
]
UNCERTAIN = "不確定"

SCHEMA = {
    "type": "object",
    "properties": {
        "channel": {"type": "string", "enum": [*CHANNELS, UNCERTAIN]},
        "headline": {"type": "string"},
        "subtitle": {"type": "string"},
        "headline_complete": {"type": "boolean"},
        "ticker": {"type": "string"},
    },
    "required": ["channel", "headline", "subtitle", "headline_complete", "ticker"],
}

# subtitle 欄位的用途是給受訪者字幕一個去處。第一版 prompt 沒有這個欄位，
# 模型在「標題條位置放的是受訪者字幕」的畫面上，15 張有 4–7 張把字幕填進
# headline——字幕會被當成查核主張送出去。加上後同一組畫面降到 1／1／0 張
# （原圖／輕度／重度翻拍）；同一 prompt 重跑兩次在原圖與輕度翻拍上零差異，
# 所以這個降幅不是雜訊。
#
# v2 的副作用：畫面上沒有這則的標題時，模型改拿別則新聞的快訊框來填
# （原圖、輕度翻拍各 1 張）。v1 拿字幕、v2 拿快訊框是同一個毛病——欄位空著
# 就找東西補——所以第 5 點直接講明留空是正確答案，而不是只再列一種不能填的文字。
#
# 範例文字刻意自己編，不取自 evals/tv_news 的畫面：取自評測集等於把考題
# 寫進 prompt，量出來的分數會虛高。
PROMPT = f"""這是一張電視新聞畫面的照片或截圖。畫面上通常同時有好幾塊文字，請分清楚各是什麼：

1. channel：畫面上的電視台台標是哪一台。只能從這份清單挑：{"、".join(CHANNELS)}。
   看不到台標、台標被遮住或模糊到無法確定，一律填「{UNCERTAIN}」。
   不要憑畫面配色、版型或主播長相推測。
2. headline：這則新聞的標題，逐字照抄。
   標題是整則新聞的摘要，同一則新聞播出期間都不變，常是兩段短語、帶「!」「?」或引號，
   例如「喝咖啡防失智? 醫:每天兩杯剛好」。
   多半在畫面下方的大字標題條，也可能是畫面側邊的直式標題。
   但畫面上常同時有別則新聞的快訊，那些不是這則的標題：
   - 側邊或角落的框，上方有兩三個字的類別標籤（例如「即時」「地震」「選舉」）
   - 和字幕條分開、另外框起來的小標題
3. subtitle：有人正在說話的字幕，逐字照抄；沒有就留空字串。
   字幕是口語、常只有半句話，例如「那其實我們在門診也常常看到」「所以建議大家不要空腹吃」。
   它的上方或旁邊常標示說話者，例如「家醫科醫師 王小明」「民眾」。
   字幕即使出現在平常放標題的那條橫幅裡，也要填在 subtitle，不可以填進 headline。
   但主播的名字標示（例如「午間新聞主播 陳小華」）下方仍可能是真正的標題，
   要看文字本身是新聞摘要還是口語。
4. 以下都不是標題：別則新聞的快訊框、跑馬燈、節目名稱、畫面中的資訊圖卡、時間、股價、氣溫、台名。
5. 畫面上沒有這則新聞的標題很常見（例如正在播受訪者講話），這時 headline 留空字串就是正確答案。
   不要拿畫面上其他看起來像標題的文字來補。
6. headline_complete：標題是否完整可讀（沒有被截斷、遮擋或模糊到缺字）。
7. ticker：畫面最底下的跑馬燈文字，逐字照抄；沒有就留空字串。

只輸出符合 schema 的結果。"""


def _normalize(text: str) -> str:
    """只留文字與數字再比對。

    跟手寫評測的 `_normalize` 刻意不同：手寫要量的就是標點抄得對不對；這裡
    抽出的標題是要拿去當查核主張與檢索關鍵字，直引號／「」、全形半形、驚嘆號
    都不影響語意，算成錯只會讓 CER 虛高，看不出真正讀錯字的比例。
    """
    text = unicodedata.normalize("NFKC", text)
    return "".join(ch for ch in text if unicodedata.category(ch)[0] in "LN")


async def _ask(service: GeminiService, path: Path, timeout: float) -> dict[str, Any]:
    try:
        payload = await asyncio.wait_for(
            service.invoke_structured_output_with_image(
                prompt=PROMPT,
                image_bytes=path.read_bytes(),
                mime_type=_mime_for(path),
                json_schema=SCHEMA,
            ),
            timeout=timeout,
        )
    except (GeminiError, asyncio.TimeoutError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(payload, dict):
        return {"error": "回應不是物件"}
    return payload


def load_cases(golden: Path) -> list[dict[str, Any]]:
    cases = []
    for line in golden.read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(json.loads(line))
    return cases


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    answered = [r for r in rows if "error" not in r["pred"]]
    visible = [r for r in answered if r.get("logo_visible", True)]
    no_logo = [r for r in answered if not r.get("logo_visible", True)]
    correct = [r for r in visible if r["pred"]["channel"] == r["channel"]]
    uncertain = [r for r in visible if r["pred"]["channel"] == UNCERTAIN]
    wrong = [
        r for r in visible
        if r["pred"]["channel"] not in (r["channel"], UNCERTAIN)
    ]

    labeled = [r for r in answered if r.get("headline")]
    cers = [
        cer(_normalize(r["headline"]), _normalize(r["pred"]["headline"]))
        for r in labeled
    ]
    missed = [r for r in labeled if not r["pred"]["headline"].strip()]
    # 畫面上確實沒有主標題（標註為空字串）時，模型還是抓出一段——多半是受訪者
    # 字幕或跑馬燈。這段會被當成查核主張送出去，是比漏抓更糟的錯。
    no_headline = [r for r in answered if r.get("headline") == ""]
    false_headline = [r for r in no_headline if r["pred"]["headline"].strip()]
    flagged_complete = [
        c for r, c in zip(labeled, cers) if r["pred"]["headline_complete"]
    ]
    flagged_incomplete = [
        c for r, c in zip(labeled, cers) if not r["pred"]["headline_complete"]
    ]

    def mean(xs: Sequence[float]) -> Optional[float]:
        return round(statistics.mean(xs), 4) if xs else None

    return {
        "n": len(rows),
        "errors": len(rows) - len(answered),
        "channel_correct": len(correct),
        "channel_uncertain": len(uncertain),
        "channel_wrong": len(wrong),
        "channel_wrong_cases": [
            {"image": r["image"], "truth": r["channel"], "pred": r["pred"]["channel"]}
            for r in wrong
        ],
        "no_logo_cases": [
            {"image": r["image"], "truth": r["channel"], "pred": r["pred"]["channel"]}
            for r in no_logo
        ],
        "headline_labeled": len(labeled),
        "headline_mean_cer": mean(cers),
        "headline_exact": sum(1 for c in cers if c == 0),
        "headline_cer_le_0.1": sum(1 for c in cers if c <= 0.1),
        "headline_missed": len(missed),
        "no_headline_frames": len(no_headline),
        "false_headline": len(false_headline),
        "false_headline_cases": [
            {"image": r["image"], "pred": r["pred"]["headline"]} for r in false_headline
        ],
        "cer_when_model_says_complete": mean(flagged_complete),
        "cer_when_model_says_incomplete": mean(flagged_incomplete),
        "n_model_says_incomplete": len(flagged_incomplete),
    }


def print_report(variant: str, s: dict[str, Any]) -> None:
    n = s["channel_correct"] + s["channel_uncertain"] + s["channel_wrong"]
    print(f"\n=== {variant}（{s['n']} 張，失敗 {s['errors']}）===")
    print(
        f"台別（有台標 {n} 張）：對 {s['channel_correct']}｜不確定 {s['channel_uncertain']}"
        f"｜答錯 {s['channel_wrong']}"
    )
    for w in s["channel_wrong_cases"]:
        print(f"  ✗ {w['image']}  實際 {w['truth']} → 模型 {w['pred']}")
    for w in s["no_logo_cases"]:
        print(f"  （無台標）{w['image']}  實際 {w['truth']} → 模型 {w['pred']}")
    if s["headline_labeled"]:
        print(
            f"主標題：已標 {s['headline_labeled']}｜平均 CER {s['headline_mean_cer']}"
            f"｜完全正確 {s['headline_exact']}｜CER≤0.1 {s['headline_cer_le_0.1']}"
            f"｜漏抓 {s['headline_missed']}"
        )
    if s["no_headline_frames"]:
        print(
            f"無主標題畫面 {s['no_headline_frames']} 張｜模型硬抓出標題 {s['false_headline']}"
        )
        for f in s["false_headline_cases"]:
            print(f"  ✗ {f['image']} → {f['pred']!r}")
        print(
            f"  模型自評完整時 CER {s['cer_when_model_says_complete']}"
            f"｜自評不完整（{s['n_model_says_incomplete']} 張）時 CER"
            f" {s['cer_when_model_says_incomplete']}"
        )


async def run(cases, model_name: str, concurrency: int, timeout: float):
    service = GeminiService(api_key=settings.GEMINI_API_KEY, model_name=model_name)
    semaphore = asyncio.Semaphore(concurrency)

    async def one(case):
        async with semaphore:
            pred = await _ask(service, _PROJECT_ROOT / case["image"], timeout)
        print(f"  {case['image']} → {pred.get('channel', pred.get('error'))}", flush=True)
        return {**case, "pred": pred}

    return await asyncio.gather(*(one(c) for c in cases))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--model", default=None, help="預設 settings.MODEL_NAME")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if not settings.GEMINI_API_KEY:
        raise SystemExit("缺 GEMINI_API_KEY")
    model_name = args.model or settings.MODEL_NAME
    cases = load_cases(args.golden)
    print(f"樣本 {len(cases)} 張｜模型 {model_name}")

    rows = asyncio.run(run(cases, model_name, args.concurrency, args.timeout))

    by_variant: dict[str, list] = defaultdict(list)
    for r in rows:
        by_variant[r.get("variant", "default")].append(r)
    report = {"model": model_name, "variants": {}, "rows": rows}
    for variant, vrows in sorted(by_variant.items()):
        s = summarize(vrows)
        print_report(variant, s)
        report["variants"][variant] = s

    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n報告寫到 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
