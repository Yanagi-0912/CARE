"""RAG eval 純函式：載入 golden JSONL、命中判定、摘要（便於 DI／單元測試）。"""

from __future__ import annotations

import json
import math
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
    expected_source_substrings: list[str] = field(default_factory=list)
    expected_content_substrings: list[str] = field(default_factory=list)
    expected_title_substrings: list[str] = field(default_factory=list)
    must_not_answer: bool = False
    notes: str = ""
    split: str = ""

    @property
    def has_retrieval_expectations(self) -> bool:
        return bool(
            self.expected_url_substrings
            or self.expected_source_substrings
            or self.expected_content_substrings
            or self.expected_title_substrings
        )


@dataclass
class CaseResult:
    id: str
    query: str
    route: str
    skipped: bool
    retrieval_hit: Optional[bool]
    retrieved_urls: list[str]
    retrieved_sources: list[str] = field(default_factory=list)
    retrieved_titles: list[str] = field(default_factory=list)
    source_hit: Optional[bool] = None
    refuse_ok: Optional[bool] = None
    answer_preview: Optional[str] = None
    error: Optional[str] = None
    mrr: Optional[float] = None
    ndcg_at_5: Optional[float] = None
    rank_mode: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EvalSummary:
    total_cases: int
    scored_cases: int
    hits: int
    hit_rate: Optional[float]
    mean_mrr: Optional[float]
    mean_ndcg_at_5: Optional[float]
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


def source_names_from_docs(docs: list[Document]) -> list[str]:
    names: list[str] = []
    for doc in docs:
        name = str(doc.metadata.get("source_name") or "").strip()
        if name:
            names.append(name)
    return names


def titles_from_docs(docs: list[Document]) -> list[str]:
    titles: list[str] = []
    for doc in docs:
        title = str(doc.metadata.get("original_title") or "").strip()
        if title:
            titles.append(title)
    return titles


def is_substring_hit(values: list[str], expected_substrings: list[str]) -> bool:
    if not values or not expected_substrings:
        return False
    for value in values:
        for substr in expected_substrings:
            if substr and substr in value:
                return True
    return False


def is_retrieval_hit(urls: list[str], expected_substrings: list[str]) -> bool:
    return is_substring_hit(urls, expected_substrings)


def contents_from_docs(docs: list[Document]) -> list[str]:
    return [doc.page_content or "" for doc in docs]


def is_doc_retrieval_hit(
    docs: list[Document],
    *,
    expected_url_substrings: list[str],
    expected_source_substrings: list[str],
    expected_content_substrings: list[str] | None = None,
    expected_title_substrings: list[str] | None = None,
) -> bool:
    if is_substring_hit(urls_from_docs(docs), expected_url_substrings):
        return True
    if is_substring_hit(source_names_from_docs(docs), expected_source_substrings):
        return True
    if is_substring_hit(titles_from_docs(docs), expected_title_substrings or []):
        return True
    return is_substring_hit(
        contents_from_docs(docs), expected_content_substrings or []
    )


def doc_relevances(case: EvalCase, docs: list[Document]) -> list[int]:
    """逐篇判定二元 relevance（1 = 命中任一期望 substring）。

    以單篇為單位重用 `is_doc_retrieval_hit`，因此 url／title／source／content
    的判準與 hit_rate 完全一致，指標之間不會互相矛盾。
    """
    return [
        1
        if is_doc_retrieval_hit(
            [doc],
            expected_url_substrings=case.expected_url_substrings,
            expected_source_substrings=case.expected_source_substrings,
            expected_content_substrings=case.expected_content_substrings,
            expected_title_substrings=case.expected_title_substrings,
        )
        else 0
        for doc in docs
    ]


def mrr(relevances: list[int]) -> float:
    """第一筆命中的排名倒數；全無命中回傳 0.0。"""
    for rank, rel in enumerate(relevances, start=1):
        if rel:
            return 1.0 / rank
    return 0.0


def _dcg(gains: list[int]) -> float:
    return sum(g / math.log2(i + 1) for i, g in enumerate(gains, start=1) if g)


def ndcg_at_k(relevances: list[int], k: int) -> float:
    """二元 gain 的 nDCG@k。

    IDCG 以「取回清單自身的 relevance 重排後」計算，而非語料庫全體的理想排序 ——
    golden set 沒有窮盡的相關性判準，無法得知語料庫中共有幾篇相關文件。
    此為缺完整判準時的標準做法，口徑已記於 evals/rag/README.md。
    """
    if k <= 0:
        return 0.0
    gains = relevances[:k]
    ideal = sorted(relevances, reverse=True)[:k]
    idcg = _dcg(ideal)
    if not idcg:
        return 0.0
    return _dcg(gains) / idcg


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
    from app.services.rag.answer_service import CANNOT_ANSWER_MARKERS
    from app.services.rag.fail_messages import is_rag_fail

    text = (answer_text or "").strip()
    if not text:
        return True
    if is_rag_fail(text):
        return True
    return any(marker in text for marker in CANNOT_ANSWER_MARKERS)


def _string_list_field(
    data: dict[str, Any], key: str, *, line_no: int, case_id: str
) -> list[str]:
    value = data.get(key) or []
    if not isinstance(value, list):
        raise ValueError(f"line {line_no} (id={case_id}): {key} must be a list")
    return [str(x).strip() for x in value if str(x).strip()]


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

            expected_clean = _string_list_field(
                data, "expected_url_substrings", line_no=line_no, case_id=case_id
            )
            expected_src_clean = _string_list_field(
                data, "expected_source_substrings", line_no=line_no, case_id=case_id
            )
            expected_content_clean = _string_list_field(
                data, "expected_content_substrings", line_no=line_no, case_id=case_id
            )
            expected_title_clean = _string_list_field(
                data, "expected_title_substrings", line_no=line_no, case_id=case_id
            )

            cases.append(
                EvalCase(
                    id=case_id,
                    query=query,
                    route=route,
                    expected_url_substrings=expected_clean,
                    expected_source_substrings=expected_src_clean,
                    expected_content_substrings=expected_content_clean,
                    expected_title_substrings=expected_title_clean,
                    must_not_answer=bool(data.get("must_not_answer", False)),
                    notes=str(data.get("notes") or ""),
                    split=str(data.get("split") or ""),
                )
            )
    return cases


def score_case_retrieval(case: EvalCase, docs: list[Document]) -> CaseResult:
    urls = urls_from_docs(docs)
    sources = source_names_from_docs(docs)
    titles = titles_from_docs(docs)
    # kb 且有期望 url／source／content／title 才計分；其餘 skip
    if case.route != "kb" or not case.has_retrieval_expectations:
        return CaseResult(
            id=case.id,
            query=case.query,
            route=case.route,
            skipped=True,
            retrieval_hit=None,
            retrieved_urls=urls,
            retrieved_sources=sources,
            retrieved_titles=titles,
        )
    relevances = doc_relevances(case, docs)
    return CaseResult(
        id=case.id,
        query=case.query,
        route=case.route,
        skipped=False,
        retrieval_hit=is_doc_retrieval_hit(
            docs,
            expected_url_substrings=case.expected_url_substrings,
            expected_source_substrings=case.expected_source_substrings,
            expected_content_substrings=case.expected_content_substrings,
            expected_title_substrings=case.expected_title_substrings,
        ),
        retrieved_urls=urls,
        retrieved_sources=sources,
        retrieved_titles=titles,
        mrr=mrr(relevances),
        ndcg_at_5=ndcg_at_k(relevances, 5),
    )


def summarize_results(results: list[CaseResult]) -> EvalSummary:
    scored = [r for r in results if not r.skipped and r.error is None]
    hits = sum(1 for r in scored if r.retrieval_hit is True)
    scored_n = len(scored)
    hit_rate = (hits / scored_n) if scored_n else None
    mrr_values = [r.mrr for r in scored if r.mrr is not None]
    ndcg_values = [r.ndcg_at_5 for r in scored if r.ndcg_at_5 is not None]
    return EvalSummary(
        total_cases=len(results),
        scored_cases=scored_n,
        hits=hits,
        hit_rate=hit_rate,
        mean_mrr=(sum(mrr_values) / len(mrr_values)) if mrr_values else None,
        mean_ndcg_at_5=(
            (sum(ndcg_values) / len(ndcg_values)) if ndcg_values else None
        ),
        miss_ids=[r.id for r in scored if r.retrieval_hit is False],
        skipped_ids=[r.id for r in results if r.skipped],
        error_ids=[r.id for r in results if r.error],
    )
