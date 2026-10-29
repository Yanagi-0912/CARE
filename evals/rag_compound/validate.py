"""Offline structural and pairing checks for the CARE compound evaluation set."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

CATEGORIES = {"true_compound": 300, "single_need": 200}
VARIANT_TYPES = {"colloquial", "taigi", "stt_typo", "no_punct"}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def numeric_tokens(query: str) -> list[str]:
    text = unicodedata.normalize("NFKC", query)
    text = re.sub(r"(?<=\d)點(?=\d)", ".", text)
    return re.findall(r"\d+(?:\.\d+)?", text)


def load_titles(chunks: Path) -> dict[str, str]:
    articles = {}
    for row in read_jsonl(chunks):
        identity = row.get("url") or f"{row.get('source_name')}|{row.get('original_title')}"
        aid = hashlib.sha1(identity.encode()).hexdigest()[:10]
        title = row.get("original_title") or ""
        if aid in articles and articles[aid] != title:
            raise ValueError(f"conflicting titles for {aid}")
        articles[aid] = title
    return articles


def validate(rows: list[dict], *, titles: dict[str, str] | None = None,
             expected: dict[str, int] | None = None) -> dict:
    expected = CATEGORIES if expected is None else expected
    errors, numeric_changes = [], []
    ids, bases, variants = {}, {}, defaultdict(list)
    title_cache = {}
    for index, row in enumerate(rows, 1):
        rid = row.get("id")
        tag = rid or f"line {index}"
        if not isinstance(rid, str) or not rid or rid in ids:
            errors.append(f"{tag}: missing or duplicate id")
        ids[rid] = row
        if not isinstance(row.get("query"), str) or not row["query"].strip():
            errors.append(f"{tag}: empty query")
        category = row.get("category")
        if category not in expected:
            errors.append(f"{tag}: invalid category")
        subs = row.get("subquestions")
        intents = row.get("expected_intents")
        if not isinstance(subs, list) or not isinstance(intents, int) or isinstance(intents, bool):
            errors.append(f"{tag}: invalid intent structure")
            continue
        if intents != len(subs) or (category == "single_need" and intents != 1) or (
            category == "true_compound" and intents not in (2, 3)
        ):
            errors.append(f"{tag}: intent count does not match category/subquestions")
        if [s.get("sid") for s in subs] != [f"s{i}" for i in range(1, len(subs) + 1)]:
            errors.append(f"{tag}: invalid or duplicate subquestion ids")
        for sub in subs:
            stag = f"{tag}/{sub.get('sid')}"
            route, fragments = sub.get("route"), sub.get("expected_title_substrings")
            if not sub.get("ask") or not sub.get("notes"):
                errors.append(f"{stag}: missing standalone ask or evidence notes")
            if route not in ("kb", "web") or not isinstance(fragments, list):
                errors.append(f"{stag}: invalid route/titles")
                continue
            if (route == "kb" and not fragments) or (route == "web" and fragments):
                errors.append(f"{stag}: route/title mismatch")
            if len(fragments) != len(set(fragments)):
                errors.append(f"{stag}: duplicate title fragment")
            matched = set()
            for fragment in fragments:
                if not isinstance(fragment, str) or not fragment or re.search(r"\s", fragment):
                    errors.append(f"{stag}: empty or whitespace-containing title fragment")
                    continue
                if titles is not None:
                    if fragment not in title_cache:
                        title_cache[fragment] = {aid for aid, title in titles.items() if fragment in title}
                    hits = title_cache[fragment]
                    if not hits or len(hits) > 5:
                        errors.append(f"{stag}: title fragment matches {len(hits)} articles: {fragment}")
                    matched |= hits
            if titles is not None and len(matched) > 20:
                errors.append(f"{stag}: {len(matched)} expected articles exceeds limit 20")
        if row.get("split") not in ("train", "holdout"):
            errors.append(f"{tag}: invalid split")
        if row.get("variant_of") is None:
            bases[rid] = row
            if row.get("variant_type") is not None:
                errors.append(f"{tag}: base has variant_type")
        else:
            variants[row["variant_of"]].append(row)
            if row.get("variant_type") not in VARIANT_TYPES:
                errors.append(f"{tag}: invalid variant type")
            if row.get("variant_type") == "no_punct" and any(
                unicodedata.category(c).startswith(("P", "S")) for c in row["query"]
            ):
                errors.append(f"{tag}: no_punct contains punctuation/symbols")
    expected_total = sum(expected.values())
    if len(rows) != 2 * expected_total or len(bases) != expected_total:
        errors.append(f"expected {expected_total} bases and {expected_total} variants, got {len(bases)} bases/{len(rows)} rows")
    counts = Counter(row.get("category") for row in bases.values())
    if counts != Counter(expected):
        errors.append(f"base category counts: {dict(counts)}")
    query_keys = Counter(re.sub(r"[\W_]", "", r["query"]).lower() for r in bases.values())
    if any(count > 1 for count in query_keys.values()):
        errors.append("duplicate base queries after punctuation normalization")
    for reference, paired in variants.items():
        if reference not in bases:
            errors.append(f"orphan variant reference: {reference}")
    for bid, base in bases.items():
        paired = variants.get(bid, [])
        if len(paired) != 1:
            errors.append(f"{bid}: expected one variant, got {len(paired)}")
            continue
        variant = paired[0]
        for key in ("category", "expected_intents", "subquestions", "source_wid", "source", "golden_id", "topic"):
            if variant.get(key) != base.get(key):
                errors.append(f"{bid}: variant changed {key}")
        if variant.get("split") != base.get("split"):
            errors.append(f"{bid}: base/variant split leakage")
        if variant.get("query") == base.get("query"):
            errors.append(f"{bid}: unchanged variant query")
        if numeric_tokens(variant["query"]) != numeric_tokens(base["query"]):
            numeric_changes.append({"id": bid, "base": numeric_tokens(base["query"]),
                                    "variant": numeric_tokens(variant["query"])})
    type_counts = Counter((r.get("category"), r.get("variant_type")) for group in variants.values() for r in group)
    for category, count in expected.items():
        for kind in VARIANT_TYPES:
            if type_counts[category, kind] != count // 4:
                errors.append(f"{category}/{kind}: expected {count // 4} variants, got {type_counts[category, kind]}")
        holdout = sum(r.get("split") == "holdout" for r in bases.values() if r.get("category") == category)
        if holdout != count // 5:
            errors.append(f"{category}: expected {count // 5} holdout bases, got {holdout}")
    true_rows = [r for r in bases.values() if r.get("category") == "true_compound"]
    varied = sum(r["query"].count("?") + r["query"].count("？") != 2 for r in true_rows)
    if true_rows and varied < len(true_rows) / 5:
        errors.append("fewer than 20% of true_compound bases use a form other than exactly two question marks")
    single_rows = [r for r in bases.values() if r.get("category") == "single_need"]
    multi_single = sum(r["query"].count("?") + r["query"].count("？") >= 2 for r in single_rows)
    if single_rows and multi_single <= len(single_rows) / 2:
        errors.append("single_need bases must predominantly have at least two question marks")
    return {"ok": not errors, "errors": errors, "rows": len(rows), "bases": len(bases),
            "base_categories": dict(counts), "holdout_rows": sum(r.get("split") == "holdout" for r in rows),
            "true_compound_non_two_question_marks": varied, "single_need_multiple_question_marks": multi_single,
            "numeric_token_changes_for_manual_review": numeric_changes,
            "kb_articles_checked": len(titles) if titles is not None else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=Path(__file__).with_name("golden.jsonl"))
    parser.add_argument("--kb-chunks", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = validate(read_jsonl(args.dataset), titles=load_titles(args.kb_chunks) if args.kb_chunks else None)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
