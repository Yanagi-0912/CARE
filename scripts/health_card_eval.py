#!/usr/bin/env python3
"""量測 n8n 影像節點的 `health_claim`：該抽的圖卡抽不抽得到，不該抽的會不會亂抽。

  python scripts/health_card_eval.py --out /tmp/health_card.json

用的是 CARE-n8n workflow 裡**原封不動**的影像 prompt，不另外寫一份——量的就是
線上那段 prompt。與 n8n 的差別只有呼叫方式（這裡直接打 Gemini，沒經過 n8n）。

兩組圖：
- 正例 `evals/health_card/golden.jsonl`：謠言長輩圖（kind=rumor）與衛教海報
  （kind=education），兩種都應該抽出主張——後端不管真假一律先查核。
- 反例：藥袋、處方箋、手寫紀錄、電視新聞畫面。這些抽出主張會被拿去查核，
  長輩只是要人念給他聽，卻收到一張判定卡。這是主要要壓低的錯誤。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

from google import genai
from google.genai import types

from app.core.config import settings
from scripts.handwriting_eval import _MIME_BY_SUFFIX

DEFAULT_WORKFLOW = _PROJECT_ROOT.parent / "CARE-n8n" / "resources" / "workflows" / "mutimedia process.json"
DEFAULT_GOLDEN = _PROJECT_ROOT / "evals" / "health_card" / "golden.jsonl"

# 反例：各資料夾抽樣，路徑相對 CARE 根目錄。
NEGATIVE_GLOBS = {
    "prescription": ("evals/prescription_scan/samples", "**/*"),
    "med_bag": ("evals/interaction_alerts/samples", "**/*"),
    "handwriting": ("evals/handwriting/aifree/samples", "*"),
    "tv_news": ("evals/tv_news/.cache/frames/clean", "*"),
}


def _workflow_prompt(path: Path) -> tuple[str, str]:
    workflow = json.loads(path.read_text(encoding="utf-8"))
    node = next(n for n in workflow["nodes"] if n["name"] == "Analyze an image")
    model = node["parameters"]["modelId"]["value"].removeprefix("models/")
    return node["parameters"]["text"], model


def _images(folder: Path, pattern: str) -> list[Path]:
    return sorted(p for p in folder.glob(pattern) if p.suffix.lower() in _MIME_BY_SUFFIX)


def _parse(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"error": "not_json", "raw": text[:200]}
    return data if isinstance(data, dict) else {"error": "not_object"}


async def _ask(client, model: str, prompt: str, path: Path, timeout: float) -> dict[str, Any]:
    part = types.Part.from_bytes(data=path.read_bytes(), mime_type=_MIME_BY_SUFFIX[path.suffix.lower()])
    try:
        response = await asyncio.wait_for(
            client.aio.models.generate_content(model=model, contents=[part, prompt]),
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - 評測腳本記下錯誤繼續跑
        return {"error": f"{type(exc).__name__}: {exc}"}
    return _parse(response.text)


def _cases(golden: Path, per_negative: int, seed: int) -> list[dict[str, Any]]:
    cases = []
    if golden.exists():
        for line in golden.read_text(encoding="utf-8").splitlines():
            if line.strip():
                cases.append({**json.loads(line), "expect_claim": True})
    rng = random.Random(seed)
    for kind, (folder, pattern) in NEGATIVE_GLOBS.items():
        images = _images(_PROJECT_ROOT / folder, pattern)
        for path in rng.sample(images, min(per_negative, len(images))):
            cases.append({
                "image": str(path.relative_to(_PROJECT_ROOT)),
                "kind": kind,
                "expect_claim": False,
            })
    return cases


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, dict[str, Any]] = {}
    for r in results:
        claim = (r["pred"].get("health_claim") or "").strip()
        ctype = r["pred"].get("content_type")
        # 電視新聞走自己的格式，n8n 不會加圖卡標記（見 Code 節點）
        marked = bool(claim) and ctype != "tv_news"
        s = by_kind.setdefault(r["kind"], {"n": 0, "marked": 0, "errors": 0, "cases": []})
        s["n"] += 1
        s["errors"] += "error" in r["pred"]
        s["marked"] += marked
        if marked != r["expect_claim"] or r["expect_claim"]:
            s["cases"].append({"image": r["image"], "expected": r.get("claim"), "got": claim, "content_type": ctype})
    return by_kind


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workflow", type=Path, default=DEFAULT_WORKFLOW)
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--per-negative", type=int, default=15)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    prompt, model = _workflow_prompt(args.workflow)
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    cases = _cases(args.golden, args.per_negative, args.seed)
    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(case):
        async with semaphore:
            pred = await _ask(client, model, prompt, _PROJECT_ROOT / case["image"], args.timeout)
        print(f"  [{case['kind']}] {case['image']} → {pred.get('health_claim', pred.get('error'))!r}", flush=True)
        return {**case, "pred": pred}

    print(f"model={model} cases={len(cases)}")
    results = await asyncio.gather(*(one(c) for c in cases))
    summary = summarize(results)
    print()
    for kind, s in summary.items():
        print(f"{kind}: 加上圖卡標記 {s['marked']}/{s['n']}（錯誤 {s['errors']}）")
    if args.out:
        args.out.write_text(
            json.dumps({"model": model, "summary": summary, "results": results}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )


if __name__ == "__main__":
    asyncio.run(main())
