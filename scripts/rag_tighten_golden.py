#!/usr/bin/env python3
"""依目前 Mongo 檢索結果，把 golden 期望收成細 path／關鍵句。

策略：在 wide retrieve 中找「關鍵詞重疊高」的 chunk；若向量排名較後
（≥3）仍有好候選，優先選它，讓 top-n 評測更能區分精排。

用法：
  python scripts/rag_tighten_golden.py
  python scripts/rag_tighten_golden.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

from app.dependencies import get_rag_retriever
from app.services.rag.eval_scoring import load_golden_jsonl

DEFAULT_GOLDEN = _PROJECT_ROOT / "evals" / "rag" / "golden.jsonl"

_STOP = {
    "的",
    "了",
    "嗎",
    "呢",
    "啊",
    "是",
    "有",
    "什麼",
    "怎麼",
    "如何",
    "哪些",
    "可以",
    "該",
    "不該",
    "要注意",
    "注意",
    "什麼時候",
    "大概",
    "多久",
    "一次",
    "做",
    "吃",
    "用",
    "對",
    "身體",
    "患者",
    "平常",
    "常見",
    "建議",
    "處理",
    "預防",
    "調整",
}


def _keywords(query: str) -> list[str]:
    # 連續詞 + 中文 bigram，提高命中率
    parts = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{2,}", query)
    chars = re.findall(r"[\u4e00-\u9fff]", query)
    bigrams = ["".join(chars[i : i + 2]) for i in range(len(chars) - 1)]
    out: list[str] = []
    for p in parts + bigrams:
        if p in _STOP or len(p) < 2:
            continue
        out.append(p)
    return list(dict.fromkeys(out))


def _kw_score(text: str, keywords: list[str]) -> int:
    t = text or ""
    return sum(1 for k in keywords if k and k in t)


def _url_marker(url: str) -> str | None:
    url = (url or "").strip()
    if not url:
        return None
    m = re.search(r"pid=\d+", url)
    if m:
        return m.group(0)
    m = re.search(r"/fact-check-reports/[^/?#]+", url)
    if m:
        return m.group(0)
    return None


def _content_marker(text: str, keywords: list[str]) -> str | None:
    text = re.sub(r"\s+", "", text or "")
    if len(text) < 12:
        return None
    for k in keywords:
        idx = text.find(k)
        if idx >= 0:
            start = max(0, idx - 4)
            end = min(len(text), idx + len(k) + 16)
            frag = text[start:end]
            if len(frag) >= 10:
                return frag
    # fallback：取前段可辨識片段
    frag = text[:24]
    return frag if len(frag) >= 10 else None


def _pick_gold(docs, keywords: list[str]):
    scored = []
    for i, doc in enumerate(docs):
        s = _kw_score(doc.page_content or "", keywords)
        scored.append((s, i, doc))
    # 優先：關鍵詞夠好且落在向量 mid-rank（5–24），這才測得出精排
    hard = [x for x in scored if 5 <= x[1] <= 24 and x[0] >= 2]
    if hard:
        hard.sort(key=lambda x: (-x[0], x[1]))
        return hard[0][2], hard[0][1], hard[0][0]
    mid = [x for x in scored if 3 <= x[1] <= 24 and x[0] >= 1]
    if mid:
        mid.sort(key=lambda x: (-x[0], x[1]))
        return mid[0][2], mid[0][1], mid[0][0]
    good = [x for x in scored if x[0] >= 1]
    if good:
        good.sort(key=lambda x: (-x[0], x[1]))
        return good[0][2], good[0][1], good[0][0]
    return docs[0], 0, 0


async def tighten(path: Path, *, dry_run: bool) -> None:
    cases = load_golden_jsonl(path)
    retriever = get_rag_retriever()
    out_rows: list[dict] = []

    for case in cases:
        row = {
            "id": case.id,
            "query": case.query,
            "route": case.route,
            "expected_url_substrings": list(case.expected_url_substrings),
            "expected_source_substrings": list(case.expected_source_substrings),
            "expected_content_substrings": list(case.expected_content_substrings),
            "must_not_answer": case.must_not_answer,
            "notes": case.notes,
            "split": case.split,
        }
        if case.route != "kb":
            # 清掉空陣列可選欄位以保持簡潔
            if not row["expected_url_substrings"]:
                row.pop("expected_url_substrings", None)
            if not row["expected_source_substrings"]:
                row.pop("expected_source_substrings", None)
            if not row["expected_content_substrings"]:
                row.pop("expected_content_substrings", None)
            out_rows.append(row)
            continue

        docs = await retriever.ainvoke(case.query)
        if not docs:
            row["notes"] = (case.notes + " | tighten: no docs").strip(" |")
            out_rows.append(row)
            print(f"{case.id}: NO DOCS")
            continue

        kws = _keywords(case.query)
        gold, rank, score = _pick_gold(docs, kws)
        url = str(gold.metadata.get("url") or "").strip()
        url_mark = _url_marker(url)
        content_mark = _content_marker(gold.page_content or "", kws)

        # 細標：不再用粗 domain／來源名稱灌水
        row["expected_url_substrings"] = [url_mark] if url_mark else []
        row["expected_source_substrings"] = []
        row["expected_content_substrings"] = [content_mark] if content_mark else []
        if not row["expected_url_substrings"] and not row["expected_content_substrings"]:
            # 最後手段：source（仍比沒標好）
            src = str(gold.metadata.get("source_name") or "").strip()
            if src:
                row["expected_source_substrings"] = [src[:6] or src]

        note = f"tighten: gold_rank={rank} kw={score} url={url_mark or '-'}"
        row["notes"] = note
        out_rows.append(row)
        print(
            f"{case.id}: rank={rank} kw={score} "
            f"url={url_mark} content={(content_mark or '')[:20]}"
        )

    if dry_run:
        print(f"dry-run: would write {len(out_rows)} rows to {path}")
        return

    with path.open("w", encoding="utf-8") as fh:
        for row in out_rows:
            # 穩定欄位順序
            ordered = {
                "id": row["id"],
                "query": row["query"],
                "route": row["route"],
            }
            for key in (
                "expected_url_substrings",
                "expected_source_substrings",
                "expected_content_substrings",
            ):
                if key in row and row[key]:
                    ordered[key] = row[key]
            ordered["must_not_answer"] = bool(row.get("must_not_answer", False))
            if row.get("notes"):
                ordered["notes"] = row["notes"]
            if row.get("split"):
                ordered["split"] = row["split"]
            fh.write(json.dumps(ordered, ensure_ascii=False) + "\n")
    print(f"wrote: {path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(tighten(args.golden, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
