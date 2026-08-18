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

# 查核判定卡的五種合法判定（openspec claim-verdict-card/specs/rag-eval/spec.md
# 原文）。獨立宣告而不從 app.services.rag.claim_verification.service 匯入，
# 是刻意的：那個模組會牽出 GeminiService／Mongo 的匯入鏈，這裡只需要五個
# 字串值，不需要跟著背負查核服務的重依賴；也呼應 brief「不要動
# claim_verification/ 底下任何檔案」的邊界——eval 這端只認字串，不依賴
# 查核服務的型別。
VALID_VERDICTS = frozenset({"錯誤", "部分錯誤", "正確", "事實釐清", "證據不足"})

# 未命中已查核主張時的固定判定，與 claim_verification/service.py 的
# NOT_ENOUGH_EVIDENCE 同值——誤配率只看「這題本該是這個判定，系統卻換成
# 別的判定」，因此需要這個值來界定誤配母體。
_NOT_ENOUGH_EVIDENCE = "證據不足"

# 判定嚴重度序（task-8-brief.md 的校準結論）：兩端是「正確」與「錯誤」，
# 中間依對使用者的誤導程度插入「事實釐清」「證據不足」「部分錯誤」。序上
# 距離 1 的誤判（例如期望「錯誤」、實得「部分錯誤」）使用者仍被告知該說法
# 有問題，是可接受的偏差；距離越大代表判定方向被誤導得越嚴重，最極端是
# 「正確」／「錯誤」兩端顛倒。
_VERDICT_SEVERITY_ORDER: tuple[str, ...] = (
    "正確",
    "事實釐清",
    "證據不足",
    "部分錯誤",
    "錯誤",
)


def verdict_severity_distance(expected: str, actual: str) -> int:
    """兩個判定在嚴重度序上的距離，供分辨「相鄰誤判」與「顛倒」使用。

    刻意不接受序外的字串：呼叫端（`score_verdict`）保證兩個引數都已通過
    `VALID_VERDICTS` 檢查，若真的出現非法值，讓 `.index()` 直接拋
    `ValueError` 比靜默歸類成任一種嚴重度更安全——誤配是本功能唯一的嚴重
    失效模式，錯誤分類不該被吞掉。
    """
    return abs(
        _VERDICT_SEVERITY_ORDER.index(expected) - _VERDICT_SEVERITY_ORDER.index(actual)
    )


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
    # 可選：查核型題目的期望判定（五種合法值之一，見 VALID_VERDICTS）。
    # 空字串＝本題不參與判定計分，既有的 hit_rate／MRR／nDCG 完全不受影響——
    # `has_retrieval_expectations` 與 `score_case_retrieval` 都不看這個欄位。
    expected_verdict: str = ""

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
    citation_count: Optional[int] = None

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
    citation_coverage: Optional[float]
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


def answer_citation_count(answer_text: str) -> int:
    """答案中出現的相異引用編號數量。

    重用 answer_service.cited_indices，確保與線上組裝來源時的判準一致。
    """
    from app.services.rag.answer_service import cited_indices

    return len(cited_indices(answer_text or ""))


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

            expected_verdict = str(data.get("expected_verdict") or "").strip()
            if expected_verdict and expected_verdict not in VALID_VERDICTS:
                raise ValueError(
                    f"line {line_no} (id={case_id}): "
                    f"expected_verdict must be one of {sorted(VALID_VERDICTS)}"
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
                    expected_verdict=expected_verdict,
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
    cited = [r for r in scored if r.citation_count is not None]
    citation_coverage = (
        sum(1 for r in cited if r.citation_count > 0) / len(cited)
    ) if cited else None
    return EvalSummary(
        total_cases=len(results),
        scored_cases=scored_n,
        hits=hits,
        hit_rate=hit_rate,
        mean_mrr=(sum(mrr_values) / len(mrr_values)) if mrr_values else None,
        mean_ndcg_at_5=(
            (sum(ndcg_values) / len(ndcg_values)) if ndcg_values else None
        ),
        citation_coverage=citation_coverage,
        miss_ids=[r.id for r in scored if r.retrieval_hit is False],
        skipped_ids=[r.id for r in results if r.skipped],
        error_ids=[r.id for r in results if r.error],
    )


@dataclass(frozen=True)
class VerdictResult:
    """單題「期望判定」與「系統實際判定」的比對結果。

    誤配（`is_mismatch_case=True`）與一般判定對錯是兩個不重疊的母體——spec
    明文「誤配 SHALL 單獨計分，SHALL NOT 併入判定正確率」。`correct` 只在
    `is_mismatch_case=False` 時有意義，`mismatched` 只在 `is_mismatch_case=True`
    時有意義；`summarize_verdicts` 依 `is_mismatch_case` 把兩者分流到不同
    分母，同一題不會同時計入判定正確率與誤配率。
    """

    id: str
    expected_verdict: str
    actual_verdict: str
    applicable: bool
    is_mismatch_case: bool = False
    correct: Optional[bool] = None
    mismatched: Optional[bool] = None
    error_kind: Optional[str] = None  # "相鄰" 或 "顛倒"；僅判定錯誤（非誤配）時有值

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_verdict(case: EvalCase, actual_verdict: str) -> VerdictResult:
    """比對單一題目的期望判定與系統實際回傳的判定。

    沒有標 `expected_verdict` 的題目回傳 `applicable=False`；呼叫端可以對
    每一題都呼叫這個函式，不必自己先篩過──`summarize_verdicts` 會濾掉
    不適用的題目，語意對齊 `score_case_retrieval` 用 `skipped` 表達「本題
    不參與這項計分」的既有慣例。
    """
    expected = case.expected_verdict
    if not expected:
        return VerdictResult(
            id=case.id,
            expected_verdict="",
            actual_verdict=actual_verdict,
            applicable=False,
        )

    if expected == _NOT_ENOUGH_EVIDENCE:
        return VerdictResult(
            id=case.id,
            expected_verdict=expected,
            actual_verdict=actual_verdict,
            applicable=True,
            is_mismatch_case=True,
            mismatched=actual_verdict != _NOT_ENOUGH_EVIDENCE,
        )

    correct = actual_verdict == expected
    error_kind = None
    if not correct:
        distance = verdict_severity_distance(expected, actual_verdict)
        error_kind = "相鄰" if distance == 1 else "顛倒"
    return VerdictResult(
        id=case.id,
        expected_verdict=expected,
        actual_verdict=actual_verdict,
        applicable=True,
        correct=correct,
        error_kind=error_kind,
    )


@dataclass(frozen=True)
class VerdictSummary:
    scored_cases: int  # 判定正確率分母：有 expected_verdict 且非「證據不足」的題數
    correct: int
    verdict_accuracy: Optional[float]
    wrong_ids: list[str]
    adjacent_wrong_ids: list[str]
    reversed_wrong_ids: list[str]
    mismatch_cases: int  # 誤配率分母：expected_verdict 為「證據不足」的題數
    mismatches: int
    mismatch_rate: Optional[float]
    mismatch_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_verdicts(results: list[VerdictResult]) -> VerdictSummary:
    """彙總判定正確率與誤配率。

    兩個指標的分母互斥（見 `VerdictResult` docstring）：`expected_verdict`
    為「證據不足」的題目只算進誤配率，其餘有標 `expected_verdict` 的題目
    只算進判定正確率。這是刻意的設計，不是疏漏——brief 記錄的教訓是「誤配
    混進整體正確率會被稀釋看不見」，分母互斥才能讓兩個指標各自誠實反映
    一件事，不會互相稀釋。
    """
    applicable = [r for r in results if r.applicable]
    accuracy_pop = [r for r in applicable if not r.is_mismatch_case]
    mismatch_pop = [r for r in applicable if r.is_mismatch_case]

    scored = len(accuracy_pop)
    correct = sum(1 for r in accuracy_pop if r.correct)
    wrong = [r for r in accuracy_pop if not r.correct]

    mismatch_total = len(mismatch_pop)
    mismatches = sum(1 for r in mismatch_pop if r.mismatched)

    return VerdictSummary(
        scored_cases=scored,
        correct=correct,
        verdict_accuracy=(correct / scored) if scored else None,
        wrong_ids=[r.id for r in wrong],
        adjacent_wrong_ids=[r.id for r in wrong if r.error_kind == "相鄰"],
        reversed_wrong_ids=[r.id for r in wrong if r.error_kind == "顛倒"],
        mismatch_cases=mismatch_total,
        mismatches=mismatches,
        mismatch_rate=(mismatches / mismatch_total) if mismatch_total else None,
        mismatch_ids=[r.id for r in mismatch_pop if r.mismatched],
    )
