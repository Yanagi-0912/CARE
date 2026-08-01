"""RAG eval 純函式：載入 golden JSONL、命中判定、摘要（便於 DI／單元測試）。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from langchain_core.documents import Document

VALID_ROUTES = frozenset({"kb", "refuse", "web"})

_SOURCE_URL_RE = re.compile(
    r"(?m)^(?:\[[^\]]+\]\s*)?(?:[^：:\n]+[：:]\s*)?(https?://\S+)\s*$"
)


@dataclass(frozen=True)
class EvalCase:
    id: str
    query: str
    route: str
    expected_url_substrings: list[str] = field(default_factory=list)
    must_not_answer: bool = False
    notes: str = ""
    split: str = ""


@dataclass
class CaseResult:
    id: str
    query: str
    route: str
    skipped: bool
    retrieval_hit: Optional[bool]
    retrieved_urls: list[str]
    source_hit: Optional[bool] = None
    refuse_ok: Optional[bool] = None
    answer_preview: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvalSummary:
    total_cases: int
    scored_cases: int
    hits: int
    hit_rate: Optional[float]
    miss_ids: list[str]
    skipped_ids: list[str]
    error_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def urls_from_docs(docs: list[Document]) -> list[str]:
    urls: list[str] = []
    for doc in docs:
        url = str(doc.metadata.get("url") or "").strip()
        if url:
            urls.append(url)
    return urls


def is_retrieval_hit(urls: list[str], expected_substrings: list[str]) -> bool:
    if not urls or not expected_substrings:
        return False
    for url in urls:
        for substr in expected_substrings:
            if substr and substr in url:
                return True
    return False


def urls_from_answer_sources(answer_text: str) -> list[str]:
    text = answer_text or ""
    marker = "參考資料來源："
    if marker not in text:
        return []
    section = text.split(marker, 1)[1]
    urls: list[str] = []
    for line in section.splitlines():
        match = _SOURCE_URL_RE.match(line.strip())
        if match:
            urls.append(match.group(1).rstrip("。；;,"))
    return urls


def is_source_hit(answer_text: str, expected_substrings: list[str]) -> bool:
    return is_retrieval_hit(urls_from_answer_sources(answer_text), expected_substrings)


def is_refuse_ok(answer_text: str) -> bool:
    from app.services.rag.answer_service import (
        CANNOT_ANSWER_MARKERS,
        NO_ANSWER_MESSAGE,
        NO_HITS_MESSAGE,
    )

    text = (answer_text or "").strip()
    if not text:
        return True
    if text in (NO_HITS_MESSAGE, NO_ANSWER_MESSAGE):
        return True
    return any(marker in text for marker in CANNOT_ANSWER_MARKERS)


def load_golden_jsonl(path: Path) -> list[EvalCase]:
    if not path.is_file():
        raise FileNotFoundError(f"golden file not found: {path}")

    cases: list[EvalCase] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at line {line_no}: {exc}") from exc

            case_id = str(data.get("id") or "").strip()
            query = str(data.get("query") or "").strip()
            route = str(data.get("route") or "").strip()
            if not case_id or not query or not route:
                raise ValueError(
                    f"line {line_no} (id={case_id or 'missing'}): "
                    "requires id, query, route"
                )
            if route not in VALID_ROUTES:
                raise ValueError(
                    f"line {line_no} (id={case_id}): "
                    f"route must be one of {sorted(VALID_ROUTES)}"
                )

            expected = data.get("expected_url_substrings") or []
            if not isinstance(expected, list):
                raise ValueError(
                    f"line {line_no} (id={case_id}): "
                    "expected_url_substrings must be a list"
                )
            expected_clean = [str(x).strip() for x in expected if str(x).strip()]

            cases.append(
                EvalCase(
                    id=case_id,
                    query=query,
                    route=route,
                    expected_url_substrings=expected_clean,
                    must_not_answer=bool(data.get("must_not_answer", False)),
                    notes=str(data.get("notes") or ""),
                    split=str(data.get("split") or ""),
                )
            )
    return cases


def score_case_retrieval(case: EvalCase, docs: list[Document]) -> CaseResult:
    urls = urls_from_docs(docs)
    # kb 且有期望來源才計分；其餘 skip（web／refuse／未標來源）
    if case.route != "kb" or not case.expected_url_substrings:
        return CaseResult(
            id=case.id,
            query=case.query,
            route=case.route,
            skipped=True,
            retrieval_hit=None,
            retrieved_urls=urls,
        )
    return CaseResult(
        id=case.id,
        query=case.query,
        route=case.route,
        skipped=False,
        retrieval_hit=is_retrieval_hit(urls, case.expected_url_substrings),
        retrieved_urls=urls,
    )


def summarize_results(results: list[CaseResult]) -> EvalSummary:
    scored = [r for r in results if not r.skipped and r.error is None]
    hits = sum(1 for r in scored if r.retrieval_hit is True)
    scored_n = len(scored)
    hit_rate = (hits / scored_n) if scored_n else None
    return EvalSummary(
        total_cases=len(results),
        scored_cases=scored_n,
        hits=hits,
        hit_rate=hit_rate,
        miss_ids=[r.id for r in scored if r.retrieval_hit is False],
        skipped_ids=[r.id for r in results if r.skipped],
        error_ids=[r.id for r in results if r.error],
    )
