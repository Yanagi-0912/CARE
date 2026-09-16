#!/usr/bin/env python3
"""問卷素材：同一批題目讓幾個 RAG 版本各答一次，存完整答案、等待秒數與實際走的路。

為什麼不用 `rag_eval.py --with-answer`：它只留答案前 240 字，而且一個 process
只能是一種設定——元件在 `app.dependencies` import 時就依 settings 組好了。
問卷要整段答案給人讀，還要同一題在各版本之間並排比。

版本（見 `VARIANTS`）只切檢索與把關元件，prompt、知識庫、來源檢查全是今天的。
所以 A 是「早期的檢索流程＋今天的其他部分」，不是當時原封不動的系統；切回舊
commit 也一樣，知識庫是共用的。

題目是 golden.jsonl 扣掉兩類：
  - 查核型（有 expected_verdict）：走 verify_claim，不經過這條管線，各版本沒差
  - route=refuse：agent 層的 guardrail 就擋掉了；這裡直接呼叫 answer service
    會繞過它，量到的不是使用者看到的（evals/rag/README.md 名詞澄清 B）

用法（專案根目錄，需先 source .venv）：
  python scripts/rag_survey_dump.py                      # A B C 各跑一次
  python scripts/rag_survey_dump.py --variants A B C C   # C 多跑一輪當對照組
  python scripts/rag_survey_dump.py --ids kb-006 kb-033  # 只跑指定題目

三件必須先知道的事：

1. **逐題依序跑、不併發。** 秒數要當成使用者等待時間，併發會互相拖慢。每題的
   版本順序會輪換（A B C → B C A → C A B），API 延遲隨時間漂移時才不會全算到
   同一個版本頭上。
2. **每一輪各有自己的 LinkChecker。** 共用的話，先跑的付 HEAD 請求、後跑的
   直接命中快取，秒數會系統性偏向後跑的那個版本。
3. **網搜不建知識回報。** 這裡的 WebSearchService 不接
   `on_web_fallback_success`。線上那條也要有 LINE user id 才會建、腳本裡本來
   就沒有；不接是讓這件事不必依賴那個前提。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import statistics
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.core.rag_sources import (  # noqa: E402  （必須在 sys.path 補好之後）
    begin_request_rag_sources,
    get_request_rag_sources,
    reset_request_rag_sources,
)
from app.services.rag.eval_scoring import EvalCase, load_golden_jsonl  # noqa: E402
from app.services.rag.fail_messages import (  # noqa: E402
    RAG_ERR_PREFIX,
    is_rag_fail,
    parse_rag_fail_code,
)

DEFAULT_GOLDEN = _PROJECT_ROOT / "evals" / "rag" / "golden.jsonl"
DEFAULT_OUT_DIR = _PROJECT_ROOT / "evals" / "rag" / "survey"

OUTCOMES = ("kb", "web", "no_answer")
OUTCOME_NAMES = {"kb": "知識庫", "web": "網搜", "no_answer": "查不到", "error": "錯誤"}
PAIR_LAYERS = ("outcome", "sources", "wording", "error")


@dataclass(frozen=True)
class VariantSpec:
    key: str
    label: str
    hybrid: bool  # 向量＋BM25 混合檢索（凸組合）；False＝純向量
    cohere: bool  # Cohere 精排；False＝依向量分數取 top-n
    crag: bool  # CRAG 分級（含 ambiguous 改寫第二輪）；False＝撈到什麼就拿去生成


VARIANTS: dict[str, VariantSpec] = {
    "A": VariantSpec("A", "早期：純向量、無精排、無 CRAG", hybrid=False, cohere=False, crag=False),
    "B": VariantSpec("B", "CRAG：純向量＋Cohere 精排＋CRAG", hybrid=False, cohere=True, crag=True),
    "C": VariantSpec(
        "C", "現行：混合檢索（凸組合）＋Cohere 精排＋CRAG", hybrid=True, cohere=True, crag=True
    ),
}


@dataclass
class RunResult:
    run: str
    variant: str
    case_id: str
    query: str
    route: str
    answer: str = ""
    display: str = ""
    seconds: Optional[float] = None
    path: Optional[str] = None
    outcome: str = "error"
    fail_code: Optional[str] = None
    top_rerank: Optional[float] = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    stages: list[dict[str, str]] = field(default_factory=list)
    error: Optional[str] = None


def select_cases(cases: Iterable[EvalCase], ids: Sequence[str] = ()) -> list[EvalCase]:
    """問卷題目：扣掉查核型與拒答題（理由見模組 docstring）。指定 ids 時只留那幾題。"""
    picked = [c for c in cases if not c.expected_verdict and c.route != "refuse"]
    if not ids:
        return picked
    wanted = set(ids)
    picked = [c for c in picked if c.id in wanted]
    missing = wanted - {c.id for c in picked}
    if missing:
        raise ValueError(f"題目不存在或屬於排除的類別: {sorted(missing)}")
    return picked


def run_labels(variant_keys: Sequence[str]) -> list[str]:
    """同一版本重複出現時編成 C、C#2……；重跑的那一輪就是對照組。"""
    seen: Counter[str] = Counter()
    labels: list[str] = []
    for key in variant_keys:
        seen[key] += 1
        labels.append(key if seen[key] == 1 else f"{key}#{seen[key]}")
    return labels


def rotated(items: Sequence[str], offset: int) -> list[str]:
    if not items:
        return []
    k = offset % len(items)
    return list(items[k:]) + list(items[:k])


_KV_RE = re.compile(r"(\w+)=(\S+)")


def parse_stage_message(message: str) -> Optional[dict[str, str]]:
    """把 `stage=<name> k=v ...`（request_logging.log_stage 的格式）拆成 dict。"""
    if not message.startswith("stage="):
        return None
    return dict(_KV_RE.findall(message))


def classify_outcome(answer: str, path: Optional[str]) -> str:
    """kb＝知識庫答出來；web＝轉網搜答出來；no_answer＝使用者拿到「查不到」類訊息。"""
    if is_rag_fail(answer):
        return "no_answer"
    if path and path.startswith("web"):
        return "web"
    return "kb"


def display_text(answer: str) -> str:
    """去掉 `[RAG_ERR:CODE]` 前綴。

    成功的答案線上是直通給使用者的（agent._route_after_tools 的 rag_direct），
    這裡看到的就是使用者看到的內文。失敗訊息不是：線上會交回 agent 的模型，
    由它改寫成降級話術，所以「查不到」那格只是素材文案，不是使用者實際讀到的字。
    """
    raw = (answer or "").strip()
    if not raw.startswith(RAG_ERR_PREFIX):
        return raw
    end = raw.find("]")
    return raw[end + 1 :].strip() if end > 0 else raw


def source_keys(result: RunResult) -> frozenset[str]:
    return frozenset(str(s.get("url") or s.get("label") or "") for s in result.sources)


def compare_pair(left: RunResult, right: RunResult) -> str:
    """同一題兩個版本差在哪一層。

    outcome＝走的路不同（知識庫／網搜／查不到），使用者一眼看得出來；
    sources＝同一條路但引用的文章不同；wording＝引用同一批文章、只有措辭不同
    ——LLM 每次措辭本來就不同，這一層的偏好就是雜訊。
    """
    if left.error or right.error:
        return "error"
    if left.outcome != right.outcome:
        return "outcome"
    if source_keys(left) != source_keys(right):
        return "sources"
    return "wording"


def _latency(rows: Iterable[RunResult]) -> dict[str, Any]:
    ok = [r for r in rows if not r.error and r.seconds is not None]
    secs = [r.seconds for r in ok]
    by_outcome: dict[str, dict[str, Any]] = {}
    for outcome in OUTCOMES:
        picked = [r.seconds for r in ok if r.outcome == outcome]
        if picked:
            by_outcome[outcome] = {"n": len(picked), "median": round(statistics.median(picked), 1)}
    return {
        "n": len(secs),
        "median": round(statistics.median(secs), 1) if secs else None,
        "max": round(max(secs), 1) if secs else None,
        "by_outcome": by_outcome,
    }


def summarize(results: Sequence[RunResult], labels: Sequence[str]) -> dict[str, Any]:
    by_run = {label: {r.case_id: r for r in results if r.run == label} for label in labels}
    case_ids = list(dict.fromkeys(r.case_id for r in results))
    pairs: list[dict[str, Any]] = []
    for i, left in enumerate(labels):
        for right in labels[i + 1 :]:
            layers: dict[str, list[str]] = {layer: [] for layer in PAIR_LAYERS}
            for case_id in case_ids:
                a, b = by_run[left].get(case_id), by_run[right].get(case_id)
                if a is not None and b is not None:
                    layers[compare_pair(a, b)].append(case_id)
            pairs.append({"pair": f"{left} vs {right}", **layers})
    return {
        "pairs": pairs,
        "outcomes": {
            label: dict(Counter(r.outcome for r in by_run[label].values())) for label in labels
        },
        "latency_seconds": {label: _latency(by_run[label].values()) for label in labels},
    }


def _fmt(value: Optional[float]) -> str:
    return "-" if value is None else f"{value:.1f}"


def render_markdown(
    meta: dict[str, Any], summary: dict[str, Any], results: Sequence[RunResult]
) -> str:
    labels: list[str] = meta["runs"]
    lines = [
        f"# RAG 問卷素材 {meta['generated_at']}",
        "",
        f"模型 `{meta['model']}`，{meta['cases']} 題。秒數是本機實測（逐題依序、不併發），不是線上。",
        "",
        "「查不到」那格是給 agent 模型的素材文案；線上使用者看到的是模型改寫過的話，"
        "做問卷時要另外決定呈現的文字，各版本統一。",
        "",
        "## 版本",
        "",
    ]
    for label in labels:
        spec = VARIANTS[label.split("#")[0]]
        suffix = "（同版本重跑，當對照組）" if "#" in label else ""
        lines.append(f"- **{label}**：{spec.label}{suffix}")

    lines += ["", "## 各版本走的路", "", "| 版本 | 知識庫 | 網搜 | 查不到 | 錯誤 |", "| --- | --- | --- | --- | --- |"]
    for label in labels:
        c = summary["outcomes"][label]
        lines.append(
            f"| {label} | {c.get('kb', 0)} | {c.get('web', 0)} | {c.get('no_answer', 0)} | {c.get('error', 0)} |"
        )

    lines += [
        "",
        "## 等待秒數",
        "",
        "| 版本 | 中位數 | 最慢 | 知識庫中位數 | 網搜中位數 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for label in labels:
        lat = summary["latency_seconds"][label]
        by = lat["by_outcome"]
        lines.append(
            f"| {label} | {_fmt(lat['median'])} | {_fmt(lat['max'])} "
            f"| {_fmt(by.get('kb', {}).get('median'))} | {_fmt(by.get('web', {}).get('median'))} |"
        )

    lines += [
        "",
        "## 兩兩比較",
        "",
        "路不同＝一邊知識庫、一邊網搜或查不到；來源不同＝同一條路但引用的文章不同；"
        "只有措辭＝引用同一批文章。",
        "",
        "| 比較 | 路不同 | 來源不同 | 只有措辭 | 錯誤 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for pair in summary["pairs"]:
        lines.append(
            f"| {pair['pair']} | {len(pair['outcome'])} | {len(pair['sources'])} "
            f"| {len(pair['wording'])} | {len(pair['error'])} |"
        )
    lines.append("")
    for pair in summary["pairs"]:
        for layer, name in (("outcome", "路不同"), ("sources", "來源不同")):
            if pair[layer]:
                lines.append(f"- {pair['pair']} {name}：{'、'.join(pair[layer])}")

    lines += ["", "## 逐題答案", ""]
    by_case: dict[str, list[RunResult]] = {}
    for r in results:
        by_case.setdefault(r.case_id, []).append(r)
    for case_id, rows in by_case.items():
        lines += [f"### {case_id}　{rows[0].query}", ""]
        for r in sorted(rows, key=lambda row: labels.index(row.run)):
            head = f"**{r.run}** · {OUTCOME_NAMES.get(r.outcome, r.outcome)} · {_fmt(r.seconds)} 秒"
            if r.path:
                head += f" · `path={r.path}`"
            lines += [head, ""]
            body = r.error or r.display or "（空白）"
            lines += [f"> {line}" if line else ">" for line in body.splitlines()]
            lines.append("")
    return "\n".join(lines) + "\n"


class StageCapture(logging.Handler):
    """收集 `stage=` 記錄。逐題依序跑，reset 之後收到的都屬於當前這題。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.stages: list[dict[str, str]] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            parsed = parse_stage_message(record.getMessage())
        except Exception:
            self.handleError(record)
            return
        if parsed is not None:
            self.stages.append(parsed)

    def reset(self) -> None:
        self.stages = []


def build_services(labels: Sequence[str]) -> dict[str, Any]:
    """每一輪各組一個 RagAnswerService（理由見模組 docstring 第 2、3 點）。

    元件取自 app.dependencies 已組好的實例，接法才會與線上一致；只有檢索器、
    精排器、分級器三處依版本替換。
    """
    import app.dependencies as deps
    from app.core.config import settings
    from app.services.rag.answer_service import RagAnswerService
    from app.services.rag.cohere_reranker import CohereReranker, VectorScoreReranker
    from app.services.rag.link_check import LinkChecker
    from app.services.rag.retriever import HybridRetriever
    from app.services.rag.web_search_service import WebSearchService

    specs = [VARIANTS[label.split("#")[0]] for label in labels]
    # .env 關掉 hybrid、少了 Cohere key 之類的情況下，這裡組出來的「C」其實是
    # 別的版本，整批答案就不能拿去做問卷——寧可現在停下來。
    problems: list[str] = []
    if any(s.hybrid for s in specs):
        if not isinstance(deps._rag_retriever, HybridRetriever):
            problems.append("混合檢索沒開（RAG_HYBRID_ENABLED／MONGODB_TEXT_INDEX）")
        elif settings.RAG_FUSION_MODE != "convex":
            problems.append(f"融合方式是 {settings.RAG_FUSION_MODE}，不是現行的 convex")
    if any(s.cohere for s in specs) and not isinstance(deps._rag_reranker, CohereReranker):
        problems.append("沒有 Cohere 精排（COHERE_API_KEY）")
    if any(s.crag for s in specs):
        if deps._rag_grader is None:
            problems.append("CRAG 分級器沒建（RAG_CRAG_ENABLED）")
        if deps._firecrawl_client is None or not settings.RAG_WEB_FALLBACK_ENABLED:
            problems.append("網搜跑不起來（FIRECRAWL_API_KEY／RAG_WEB_FALLBACK_ENABLED）")
    if problems:
        raise SystemExit("設定與版本定義不符：" + "；".join(problems))

    def new_link_checker() -> Optional[LinkChecker]:
        if not settings.RAG_LINK_CHECK_ENABLED:
            return None
        return LinkChecker(
            timeout_seconds=settings.RAG_LINK_CHECK_TIMEOUT_SECONDS,
            ok_ttl_seconds=settings.RAG_LINK_CHECK_OK_TTL_SECONDS,
            dead_ttl_seconds=settings.RAG_LINK_CHECK_DEAD_TTL_SECONDS,
        )

    services: dict[str, Any] = {}
    for label, spec in zip(labels, specs):
        # 同一輪的兩條回答路徑共用一個 checker，與線上接法相同；跨輪不共用。
        checker = new_link_checker()
        web = WebSearchService(
            gemini_service=deps._gemini_service,
            web_client=deps._firecrawl_client,
            on_web_fallback_success=None,
            link_checker=checker,
            en_search_domains=settings.RAG_WEB_SEARCH_EN_DOMAINS.split(","),
        )
        services[label] = RagAnswerService(
            gemini_service=deps._gemini_service,
            retriever=deps._rag_retriever if spec.hybrid else deps._rag_vector_retriever,
            reranker=deps._rag_reranker if spec.cohere else VectorScoreReranker(),
            rerank_top_n=settings.RAG_RERANK_TOP_N,
            max_chunks_per_article=settings.RAG_RERANK_MAX_CHUNKS_PER_ARTICLE,
            grader=deps._rag_grader if spec.crag else None,
            rewriter=deps._rag_rewriter if spec.crag else None,
            crag_rewrite_budget_seconds=settings.RAG_CRAG_REWRITE_BUDGET_SECONDS,
            speculative_generate=settings.RAG_SPECULATIVE_GENERATE,
            crag_enabled=spec.crag,
            web_search=web,
            web_fallback_enabled=settings.RAG_WEB_FALLBACK_ENABLED,
            degraded_min_score=settings.RAG_DEGRADED_MIN_SCORE,
            link_checker=checker,
        )
    return services


def describe_services(services: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """每一輪實際用到的元件類別。

    混合檢索的 BM25 那條腿沒有自己的 stage 記錄，光看 log 分不出 B 與 C 用的
    是哪個檢索器；寫進結果，事後才查得到每個版本真的是那樣組的。
    """
    return {
        label: {
            "retriever": type(svc.retriever).__name__,
            "reranker": type(svc.reranker).__name__,
            "crag_enabled": svc.crag_enabled,
            "web_fallback_enabled": svc.web_fallback_enabled,
        }
        for label, svc in services.items()
    }


def run_meta(labels: Sequence[str], cases: Sequence[EvalCase], golden: Path) -> dict[str, Any]:
    from app.core.config import settings

    keys = {label.split("#")[0] for label in labels}
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "golden": str(golden),
        "cases": len(cases),
        "runs": list(labels),
        "variants": {k: asdict(v) for k, v in VARIANTS.items() if k in keys},
        "model": settings.MODEL_NAME,
        "fusion_mode": settings.RAG_FUSION_MODE,
        "fusion_alpha": settings.RAG_FUSION_ALPHA,
        "rerank_model": settings.COHERE_RERANK_MODEL,
        "rerank_top_n": settings.RAG_RERANK_TOP_N,
        "seconds_note": "本機逐題依序實測，不是線上",
    }


async def answer_once(
    service: Any, case: EvalCase, run: str, capture: StageCapture
) -> RunResult:
    result = RunResult(
        run=run, variant=run.split("#")[0], case_id=case.id, query=case.query, route=case.route
    )
    capture.reset()
    token = begin_request_rag_sources()
    answer: Optional[str] = None
    sources: tuple = ()
    started = time.perf_counter()
    try:
        answer = await service.answer(case.query)
        sources = get_request_rag_sources()
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        result.seconds = round(time.perf_counter() - started, 2)
        reset_request_rag_sources(token)

    result.stages = list(capture.stages)
    rag_stage = next(
        (s for s in reversed(result.stages) if s.get("stage") == "rag_answer"), {}
    )
    result.path = rag_stage.get("path")
    try:
        result.top_rerank = float(rag_stage["top_rerank"])
    except (KeyError, ValueError):
        result.top_rerank = None
    if answer is not None:
        result.answer = answer
        result.display = display_text(answer)
        result.fail_code = parse_rag_fail_code(answer)
        result.outcome = classify_outcome(answer, result.path)
        result.sources = [asdict(s) for s in sources]
    return result


async def run_all(
    cases: Sequence[EvalCase],
    labels: Sequence[str],
    services: dict[str, Any],
    capture: StageCapture,
    sink: Callable[[RunResult], None],
) -> list[RunResult]:
    results: list[RunResult] = []
    total = len(cases) * len(labels)
    for i, case in enumerate(cases):
        for label in rotated(labels, i):
            r = await answer_once(services[label], case, label, capture)
            results.append(r)
            sink(r)
            status = f"ERROR {r.error}" if r.error else f"{r.outcome} path={r.path}"
            print(f"[{len(results)}/{total}] {case.id} {label} {_fmt(r.seconds)}s {status}", flush=True)
    return results


def _print_summary(summary: dict[str, Any], labels: Sequence[str]) -> None:
    print("\n== 各版本走的路")
    for label in labels:
        c = summary["outcomes"][label]
        print(
            f"  {label:<4} 知識庫 {c.get('kb', 0)}  網搜 {c.get('web', 0)}  "
            f"查不到 {c.get('no_answer', 0)}  錯誤 {c.get('error', 0)}"
        )
    print("== 等待秒數（本機實測）")
    for label in labels:
        lat = summary["latency_seconds"][label]
        print(f"  {label:<4} 中位數 {_fmt(lat['median'])}  最慢 {_fmt(lat['max'])}")
    print("== 兩兩比較（路不同／來源不同／只有措辭／錯誤）")
    for pair in summary["pairs"]:
        counts = " / ".join(str(len(pair[layer])) for layer in PAIR_LAYERS)
        print(f"  {pair['pair']:<10} {counts}")
        if pair["outcome"]:
            print(f"    路不同：{'、'.join(pair['outcome'])}")


async def _run(args: argparse.Namespace) -> int:
    cases = select_cases(load_golden_jsonl(args.golden), args.ids)
    labels = run_labels(args.variants)
    services = build_services(labels)
    meta = run_meta(labels, cases, args.golden)
    meta["components"] = describe_services(services)
    for label, parts in meta["components"].items():
        print(f"{label:<4} {parts}", flush=True)

    capture = StageCapture()
    app_logger = logging.getLogger("app")
    app_logger.addHandler(capture)
    app_logger.setLevel(logging.INFO)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"rag-survey-{datetime.now():%Y%m%d-%H%M}"
    partial = args.out_dir / f"{stem}.partial.jsonl"
    print(f"{len(cases)} 題 × {len(labels)} 輪（{' '.join(labels)}），逐筆寫入 {partial}", flush=True)

    with partial.open("w", encoding="utf-8") as fh:

        def sink(r: RunResult) -> None:
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
            fh.flush()

        results = await run_all(cases, labels, services, capture, sink)

    summary = summarize(results, labels)
    json_path = args.out_dir / f"{stem}.json"
    md_path = args.out_dir / f"{stem}.md"
    json_path.write_text(
        json.dumps(
            {"meta": meta, "summary": summary, "results": [asdict(r) for r in results]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(meta, summary, results), encoding="utf-8")
    partial.unlink()

    _print_summary(summary, labels)
    print(f"\n完整結果：{json_path}\n並排閱讀：{md_path}")
    return 1 if results and all(r.error for r in results) else 0


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG 問卷素材：同一批題目各版本的答案並排")
    parser.add_argument("--golden", type=Path, default=DEFAULT_GOLDEN)
    parser.add_argument(
        "--variants",
        nargs="+",
        choices=sorted(VARIANTS),
        default=["A", "B", "C"],
        help="要跑的版本；同一個寫兩次＝多跑一輪當對照組",
    )
    parser.add_argument("--ids", nargs="*", default=[], help="只跑這幾題（預設全部可用題目）")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    return asyncio.run(_run(_parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
