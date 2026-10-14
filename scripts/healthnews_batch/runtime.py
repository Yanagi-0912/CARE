"""將同一 CARE RAG 決策接到持久 Batch 呼叫；只供離線實驗。"""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import json
import logging
import os
import time
from types import SimpleNamespace

from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from scripts.healthnews_experiment import (Capture, ObservedDecomposer, ObservedGeminiGrader,
    ObservedGrader, ObservedJevGrader, ObservedModel, ObservedRagAnswerService,
    ObservedRetriever, _record, current, digest as event_digest, evidence_ids, observed, run_case)
from .config import digest
from app.services.rag.pgvector_retriever import PgVectorRetriever


@dataclass
class RunContext:
    key: str
    occurrences: Counter = field(default_factory=Counter)

    def next(self, label):
        value = self.occurrences[label]
        self.occurrences[label] += 1
        return value


_run = ContextVar("healthnews_batch_run", default=None)


@contextmanager
def run_scope(key):
    token = _run.set(RunContext(key))
    try:
        yield
    finally:
        _run.reset(token)


def context() -> RunContext:
    run = _run.get()
    if run is None:
        raise RuntimeError("Batch call outside an experiment run")
    return run


class RecordedStageError(RuntimeError):
    """已發生的本輪 provider 故障；不重新呼叫外部服務。"""


class BudgetExceeded(RuntimeError):
    pass


class ReadOnlyPgVectorRetriever(PgVectorRetriever):
    """隔離快照可用既有同步角色讀取，但連線強制拒絕資料寫入。"""
    async def _ensure_pool(self):
        if self._pool is None:
            import asyncpg
            self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=4, command_timeout=30,
                                                  server_settings={"default_transaction_read_only": "on"})
        if getattr(self, "diagnostics_enabled", False):
            from .diagnostics import MeasuredPool
            return MeasuredPool(self._pool)
        return self._pool

    async def _fetch_documents(self, scored):
        if not getattr(self, "diagnostics_enabled", False):
            return await super()._fetch_documents(scored)
        from .diagnostics import measured
        with measured("mongo_content_fetch"):
            return await super()._fetch_documents(scored)


class DurableCalls:
    def __init__(self, journal):
        self.journal = journal
        self.active = set()

    async def call(self, kind, inputs, operation, *, timeout_seconds=None):
        self.journal.assert_no_uncertain_receipts()
        run = context()
        hashed = digest(inputs)
        stage = f"{kind}:{hashed}:{run.next(kind + ':' + hashed)}"
        receipt = self.journal.get_receipt(run.key, stage, hashed)
        repeatable_read = kind in ("context_count_tokens", "source_link_check")
        if receipt is not None and receipt["status"] in ("read_started", "retryable_read"):
            if not repeatable_read:
                raise ValueError("non-read receipt cannot be retried")
            if _record.get() is not None:
                current().setdefault("retried_read_receipts", []).append(stage)
            receipt = None
        if receipt is not None:
            if _record.get() is not None:
                current().setdefault("resumed_stage_receipts", []).append(stage)
            if receipt["status"] == "error":
                error = receipt["payload"]["error_type"]
                if error == "TimeoutError":
                    raise TimeoutError("recorded same-run provider timeout")
                raise RecordedStageError("recorded same-run provider failure: " + error)
            return receipt["payload"]
        self.journal.save_receipt(run.key, stage, hashed, "read_started" if repeatable_read else "started", {"kind": kind})
        task = asyncio.current_task()
        self.active.add(task)
        try:
            result = await asyncio.wait_for(operation(), timeout_seconds) if timeout_seconds else await operation()
        except asyncio.CancelledError:
            self.journal.save_receipt(run.key, stage, hashed, "retryable_read" if repeatable_read else "unknown",
                                      {"error_type": "CancelledError"})
            raise
        except Exception as exc:
            self.journal.save_receipt(run.key, stage, hashed, "error", {"error_type": type(exc).__name__})
            raise
        finally:
            self.active.discard(task)
        result = json.loads(json.dumps(result, ensure_ascii=False, default=str))
        self.journal.save_receipt(run.key, stage, hashed, "done", result)
        return result


    async def close(self):
        tasks = list(self.active - {asyncio.current_task()})
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class BatchModel:
    def __init__(self, coordinator, role, model_name, thinking=None, schema=None):
        self.coordinator, self.role, self.model_name = coordinator, role, model_name
        self.thinking, self.schema = thinking, schema

    def with_structured_output(self, schema, *, method="json_schema", **kwargs):
        if method != "json_schema" or kwargs:
            raise ValueError("offline Batch only supports CARE's json_schema contract")
        return BatchModel(self.coordinator, self.role, self.model_name, self.thinking, schema)

    async def ainvoke(self, messages, *args, **kwargs):
        if args or kwargs:
            raise ValueError("unexpected model invocation overrides")
        system = [m.content for m in messages if m.type == "system"]
        contents = [{"role": "model" if m.type == "ai" else "user", "parts": [{"text": m.content}]}
                    for m in messages if m.type != "system"]
        config = {}
        if system:
            config["system_instruction"] = "\n".join(system)
        if self.thinking:
            config["thinking_config"] = {"thinking_level": self.thinking}
        if self.schema:
            config.update(response_mime_type="application/json", response_json_schema=self.schema)
        request = {"contents": contents, "config": config}
        run = context()
        hashed = digest(request)
        # Query-specific sequence avoids race-dependent ordering across concurrent subquestions.
        occurrence = f"{hashed}:{run.next(self.role + ':' + hashed)}"
        response = await self.coordinator.request(run.key, self.role, occurrence, self.model_name, request)
        if self.schema:
            result = json.loads(response["text"])
            if not isinstance(result, dict):
                raise ValueError("structured Batch response must be an object")
            if _record.get() is not None:
                current().setdefault("structured_model_metadata", []).append({
                    "role": self.role, "usage_metadata": response.get("usage_metadata"),
                    "response_metadata": response.get("response_metadata")})
            return result
        usage = response.get("usage_metadata", {})
        token_usage = {"input_tokens": usage.get("prompt_token_count", 0),
                       "output_tokens": usage.get("candidates_token_count", 0),
                       "total_tokens": usage.get("total_token_count", 0)}
        return AIMessage(content=response["text"], usage_metadata=token_usage,
                         response_metadata={**response.get("response_metadata", {}),
                                            "provider_usage_metadata": response.get("usage_metadata", {})})


class ReceiptRetriever:
    def __init__(self, retriever, calls, layer, timeout_seconds=None):
        self.retriever, self.calls, self.layer = retriever, calls, layer
        self.timeout_seconds = timeout_seconds

    async def ainvoke(self, query):
        async def fetch():
            docs = await self.retriever.ainvoke(query)
            return [{"text": d.page_content, "metadata": d.metadata} for d in docs]
        rows = await self.calls.call("retrieval_" + self.layer, {"query": query}, fetch,
                                     timeout_seconds=self.timeout_seconds)
        return [Document(page_content=r["text"], metadata=r["metadata"]) for r in rows]


class ReceiptJevGrader(ObservedJevGrader):
    def __init__(self, *args, calls, timeout_seconds=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = calls
        from app.services.rag.jev_grader import TIMEOUT_SECONDS
        self.provider_timeout = TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds

    async def grade(self, query, docs):
        # Move only the timeout boundary into the durable provider call. Keep the same threshold/fallback.
        from app.services.rag.jev_grader import grade_from_probabilities
        from app.core.request_logging import log_stage
        if not self._api_key:
            return await self._fallback.grade(query, docs)
        started = time.perf_counter()
        try:
            probabilities = await self._ask(query, docs)
            grade = grade_from_probabilities(probabilities)
        except Exception as exc:
            log_stage(logging.getLogger("app.services.rag.jev_grader"), "crag_grade_jev", outcome="fallback",
                      error=type(exc).__name__, ms=int((time.perf_counter() - started) * 1000))
            if isinstance(exc, TimeoutError):
                for event in current()["events"]:
                    if event["kind"] == "grader_provider" and event.get("provider") == "Jev" and event.get("error_type") == "TimeoutError":
                        event["status"] = "timeout"
            return await self._fallback.grade(query, docs)
        log_stage(logging.getLogger("app.services.rag.jev_grader"), "crag_grade_jev", outcome=grade.value,
                  p=round(float(probabilities.get("correct", 0)), 4), ms=int((time.perf_counter() - started) * 1000))
        return grade

    async def _ask(self, query, docs):
        from app.services.rag.jev_grader import JevRetrievalGrader, build_state, JEV_MODEL, QUESTION
        payload = {"state": build_state(query, docs), "model": JEV_MODEL, "questions": QUESTION}
        async with observed("grader_provider", provider="Jev", model=JEV_MODEL, effective_input=payload,
                            prompt_hash=event_digest(payload), max_chars_per_doc=400,
                            included_evidence_ids=evidence_ids(docs)) as event:
            result = await self.calls.call("jev", payload, lambda: JevRetrievalGrader._ask(self, query, docs),
                                           timeout_seconds=self.provider_timeout)
            event["probabilities"] = result
            return result


class ReceiptLinkChecker:
    def __init__(self, checker, calls):
        self.checker, self.calls = checker, calls

    async def alive(self, urls):
        urls = list(urls)
        async def check():
            return await self.checker.alive(urls)
        if _record.get() is None:
            return await self.calls.call("source_link_check", {"urls": urls}, check)
        async with observed("source_link_check", urls=urls) as event:
            event["statuses"] = await self.calls.call("source_link_check", {"urls": urls}, check)
            return event["statuses"]


class CandidateBudgetRetriever:
    def __init__(self, retriever, owner):
        self.retriever, self.owner = retriever, owner

    async def ainvoke(self, query):
        docs = await self.retriever.ainvoke(query)
        self.owner.candidate_used += len(docs)
        if self.owner.candidate_used > self.owner.candidate_budget:
            current()["budget_exceeded"] = True
            raise BudgetExceeded("candidate budget exceeded")
        return docs


class OfflineRagService(ObservedRagAnswerService):
    def __init__(self, *args, calls, token_counter, candidate_budget, context_token_budget, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls, self.token_counter = calls, token_counter
        self.candidate_budget, self.context_token_budget = candidate_budget, context_token_budget
        self.candidate_used = 0
        self.retriever = CandidateBudgetRetriever(self.retriever, self)

    async def _generate_answer(self, question, docs, *, speculative=False):
        from app.services.rag.answer_prompts import wrap_context
        wrapped = wrap_context(self._build_context(docs))
        start = asyncio.get_running_loop().time()
        resource = {"kind": "context_count_tokens", "context_hash": digest(wrapped), "status": "started"}
        current().setdefault("experiment_resource_calls", []).append(resource)
        try:
            tokens = await self.calls.call("context_count_tokens", {"context": wrapped}, lambda: self.token_counter(wrapped))
            resource.update(context_tokens=tokens, status="completed")
        except asyncio.CancelledError:
            resource["status"] = "cancelled"
            raise
        except Exception as exc:
            resource.update(status="error", error_type=type(exc).__name__)
            raise
        finally:
            resource["latency_ms"] = (asyncio.get_running_loop().time() - start) * 1000
        if tokens > self.context_token_budget:
            current()["budget_exceeded"] = True
            raise BudgetExceeded("context budget exceeded")
        answer = await super()._generate_answer(question, docs, speculative=speculative)
        for event in reversed(current()["events"]):
            if event["kind"] == "generation" and event.get("context_hash") == event_digest(self._build_context(docs)):
                event["context_tokens"] = tokens
                event["context_token_method"] = "provider_count_tokens"
                break
        return answer


def _review_fields(row, version):
    row.update(config_version=version, experiment_mode="offline_batch", actual_route="RAG_direct",
               claim_reviews=[], blocking_findings=[], subquestion_reviews=[{
                   "reference_intent_id": None, "actual_subquestion_ids": [f"s{i}"],
                   "retrieved_support": "not_reviewed", "answered": "not_reviewed", "review_status": "pending"}
                   for i in range(1, max(1, len(row["actual_subquestions"])) + 1)])
    row["offline_wall_latency_ms"] = row.pop("latency_ms")
    row["interactive_latency_comparable"] = False
    if row.get("budget_exceeded") or any(e.get("error_type") == "BudgetExceeded" for e in row["runtime_errors"]):
        row["blocking_findings"].append("budget_exceeded")
    if row["fail_code"] == "KB_EMPTY":
        row["blocking_findings"].append("kb_empty_template_scope_requires_review")
    if row.get("citation_mapping_before_renumber"):
        row["final_citation_review_status"] = "pending_source_cap_alignment"
    return row


def export_results(config, journal):
    config.output.mkdir(parents=True, exist_ok=True)
    rows = journal.results()
    temporary = config.output / "runs.jsonl.tmp"
    with temporary.open("w") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    temporary.replace(config.output / "runs.jsonl")
    (config.output / "manifest.json").write_text(json.dumps({**config.identity(), "planned_runs": config.planned_runs,
        "completed_runs": len(rows), "formal_execution_requested": True,
        "corpus_mode": config.corpus_mode, "immutable_corpus": config.corpus_mode == "immutable_snapshot",
        "corpus_verification_file": "corpus_verification.json", "continuous_immutability_proven": False,
        "review_status": "pending_post_run_review", "interactive_timeout_applied": False}, ensure_ascii=False, indent=2) + "\n")
    return rows


async def run_experiment(config, journal, coordinator, factory, *, probe_factory=None):
    if config.blockers:
        raise ValueError("; ".join(config.blockers))
    journal.bind_config(config.version, config.identity())
    journal.assert_no_uncertain_receipts()
    config.output.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(config.raw["batch"]["concurrency"])
    export_latencies = []
    def export():
        started = time.perf_counter()
        try:
            return export_results(config, journal)
        finally:
            export_latencies.append((time.perf_counter()-started)*1000)
    capture = Capture()
    logging.getLogger().addHandler(capture)
    logging.getLogger().setLevel(logging.INFO)

    async def one(case, arm, number):
        queued_at = time.perf_counter()
        key = config.run_key(case["id"], arm, number)
        if journal.get_result(key) is not None:
            return journal.get_result(key)
        async with semaphore:
            slot_wait_ms = (time.perf_counter() - queued_at) * 1000
            with run_scope(key):
                corpus = config.raw["arms"][arm]["corpus"]
                row = await run_case(case, arm, number, factory(arm), config.snapshots[corpus]["snapshot_id"],
                                     snapshot_id=config.snapshots[corpus]["snapshot_id"], phase="live_observed" if config.corpus_mode == "live_observed" else "formal")
                row["run_slot_wait_ms"] = slot_wait_ms
                row["study_role"] = config.raw.get("study_role", "original_experiment")
                row["corpus_mode"] = config.corpus_mode
                row["immutable_corpus"] = config.corpus_mode == "immutable_snapshot"
                row = _review_fields(row, config.version)
                row["run_key"] = key
                journal.assert_no_uncertain_receipts()
                journal.save_result(key, row)
                export()
                return row
    tasks = [asyncio.create_task(one(case, arm, number))
             for number in range(1, config.raw["rounds"] + 1) for case in config.cases
             for arm in (config.raw["selected_arms"] if number % 2 else list(reversed(config.raw["selected_arms"])))]
    probe = None
    if config.raw.get("diagnostics", {}).get("enabled", False):
        from .diagnostics import LoopProbe
        probe = (probe_factory or LoopProbe)()
    try:
        if probe is not None:
            await probe.start()
        await coordinator.drive(tasks)
        rows = export()
        if len(rows) != config.planned_runs:
            raise ValueError("experiment incomplete; inspect journal before resuming")
        return rows
    finally:
        try:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if probe is not None:
                from scripts.healthnews_snapshot import save
                report = await probe.close()
                report["blocking_result_exports"] = {"count":len(export_latencies),
                    "total_ms":sum(export_latencies), "max_ms":max(export_latencies, default=0)}
                save(config.output / "diagnostics.json", report)
        finally:
            logging.getLogger().removeHandler(capture)


def build_factory(config, journal, coordinator, settings):
    """依固定設定建立 client，只有 run --submit 或 snapshot verify 入口會使用。"""
    from app.services.rag.retriever import HybridRetriever, MongoAtlasTextRetriever
    from app.services.rag.cohere_reranker import CohereReranker, _default_http_post
    from app.services.rag.link_check import LinkChecker
    from app.services.rag.question_decomposer import GeminiQuestionDecomposer
    from app.services.rag.query_rewriter import GeminiQueryRewriter
    from langchain_google_genai import GoogleGenerativeAIEmbeddings
    from google import genai

    required = ("GEMINI_API_KEY", "COHERE_API_KEY", "TYPESAFE_API_KEY", "MONGODB_URI", "PGVECTOR_DSN")
    missing = [k for k in required if not getattr(settings, k, "")]
    if missing:
        raise ValueError("missing experiment credentials: " + ",".join(missing))
    options = config.raw["settings"]
    calls = DurableCalls(journal)
    vectors, services = {}, []
    counter = genai.Client(api_key=settings.GEMINI_API_KEY)

    def model(role, thinking=None):
        name = config.raw["models"][role]
        return SimpleNamespace(chat_model=ObservedModel(BatchModel(coordinator, role, name, thinking), role, name))

    async def count_tokens(content):
        result = await counter.aio.models.count_tokens(model=config.raw["models"]["generation"], contents=content)
        return result.total_tokens

    for corpus, snapshot in config.snapshots.items():
        embeddings = GoogleGenerativeAIEmbeddings(model=config.raw["models"]["embedding"],
            google_api_key=settings.GEMINI_API_KEY, task_type="RETRIEVAL_QUERY", output_dimensionality=options["MONGODB_VECTOR_DIM"])
        snapshot_dsn = settings.PGVECTOR_DSN if config.corpus_mode == "live_observed" else (os.getenv("PGVECTOR_SNAPSHOT_DSN") or os.getenv("PGVECTOR_SYNC_DSN") or settings.PGVECTOR_DSN)
        vector = ReadOnlyPgVectorRetriever(embeddings=embeddings, dsn=snapshot_dsn, mongo_uri=settings.MONGODB_URI,
            db_name=options["MONGODB_DB"], collection_name=snapshot["mongo_collection"], table_name=snapshot["pg_table"],
            vector_column=options["PGVECTOR_VECTOR_COLUMN"], text_field=options["MONGODB_TEXT_FIELD"],
            vector_dim=options["MONGODB_VECTOR_DIM"], k=options["RAG_RETRIEVE_CANDIDATES"], min_score=options["RAG_VECTOR_MIN_SCORE"])
        text = MongoAtlasTextRetriever(mongo_uri=settings.MONGODB_URI, db_name=options["MONGODB_DB"],
            collection_name=snapshot["mongo_collection"], index_name=snapshot["mongo_search_index"],
            text_field=options["MONGODB_TEXT_FIELD"], title_field=options["MONGODB_TEXT_TITLE_FIELD"],
            title_boost=options["RAG_TEXT_TITLE_BOOST"], k=options["RAG_RETRIEVE_CANDIDATES"])
        if config.raw.get("diagnostics", {}).get("enabled", False):
            from .diagnostics import MeasuredEmbeddings, MeasuredRetriever
            vector.diagnostics_enabled = True
            vector.embeddings = MeasuredEmbeddings(embeddings)
            text = MeasuredRetriever(text, "mongo_bm25")
        hybrid = HybridRetriever(vector_retriever=ObservedRetriever(ReceiptRetriever(vector, calls, "vector",
                                      options["RAG_RETRIEVE_LEG_TIMEOUT_SECONDS"]), "vector"),
            text_retriever=ObservedRetriever(ReceiptRetriever(text, calls, "bm25",
                                      options["RAG_RETRIEVE_LEG_TIMEOUT_SECONDS"]), "bm25"),
            rrf_k=options["RAG_RRF_K"], limit=options["RAG_RETRIEVE_CANDIDATES"], fusion_mode=options["RAG_FUSION_MODE"],
            alpha=options["RAG_FUSION_ALPHA"], leg_timeout_seconds=0)
        vectors[corpus] = (vector, hybrid)

    async def cohere_post(url, *, headers, json, timeout):
        async with observed("rerank_provider", provider="Cohere", effective_input=json, prompt_hash=event_digest(json)) as event:
            event["output"] = await calls.call("cohere", json, lambda: _default_http_post(url, headers=headers, json=json, timeout=timeout))
            return event["output"]

    def factory(arm):
        corpus = config.raw["arms"][arm]["corpus"]
        grader = ReceiptJevGrader(api_key=settings.TYPESAFE_API_KEY, calls=calls,
                                  fallback=ObservedGeminiGrader(gemini_service=model("crag_fallback", "low")))
        services.append(grader)
        checker = ReceiptLinkChecker(LinkChecker(timeout_seconds=options["RAG_LINK_CHECK_TIMEOUT_SECONDS"],
            ok_ttl_seconds=options["RAG_LINK_CHECK_OK_TTL_SECONDS"], dead_ttl_seconds=options["RAG_LINK_CHECK_DEAD_TTL_SECONDS"]), calls) if options["RAG_LINK_CHECK_ENABLED"] else None
        return OfflineRagService(gemini_service=model("generation"), retriever=ObservedRetriever(vectors[corpus][1], "hybrid"),
            reranker=CohereReranker(api_key=settings.COHERE_API_KEY, model=options["COHERE_RERANK_MODEL"],
                                  timeout_seconds=options["COHERE_RERANK_TIMEOUT_SECONDS"], http_post=cohere_post),
            rerank_top_n=options["RAG_RERANK_TOP_N"], max_chunks_per_article=options["RAG_RERANK_MAX_CHUNKS_PER_ARTICLE"],
            grader=ObservedGrader(grader), rewriter=GeminiQueryRewriter(gemini_service=model("rewrite", "low")),
            crag_enabled=True, web_search=None, web_fallback_enabled=False, link_checker=checker,
            degraded_min_score=options["RAG_DEGRADED_MIN_SCORE"], speculative_generate=options["RAG_SPECULATIVE_GENERATE"],
            total_timeout_seconds=0, decomposer=ObservedDecomposer(GeminiQuestionDecomposer(model("decomposition", "low")))
            if config.raw["arms"][arm]["decomposition"] else None,
            calls=calls, token_counter=count_tokens, candidate_budget=config.raw["budget"]["candidate_total_per_run"],
            context_token_budget=config.raw["budget"]["context_tokens"])

    async def close():
        await calls.close()
        for vector, _ in vectors.values():
            if vector._pool is not None:
                await vector._pool.close()
        for grader in services:
            await grader._client.aclose()
        await counter.aio.aclose()
    return factory, {corpus: v[0] for corpus, v in vectors.items()}, close
