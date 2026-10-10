"""Healthnews 逐題實驗；只將原始問題送給 CARE，不計算總分。

--phase pilot 可唯讀使用即時庫；formal 必須提供 verified_immutable 快照。
每筆保存 grader 有效輸入、證據全文、投機取消事件與完整回答，審查留待確認。
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
import time
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.rag_sources import begin_request_rag_sources, get_request_rag_sources, reset_request_rag_sources
from app.core.user_language import reset_request_language, set_request_language
from app.i18n.messages import t
from app.services.gemini.shared.parser import content_to_text
from app.services.rag.answer_service import RagAnswerService, cited_indices
from app.services.rag.fail_messages import is_rag_fail, parse_rag_fail_code
from app.services.rag.jev_grader import JEV_MODEL, QUESTION, JevRetrievalGrader, build_state
from app.services.rag.retrieval_grader import GeminiRetrievalGrader

_record = ContextVar("healthnews_record", default=None)
_parent = ContextVar("healthnews_parent", default=None)
_grade_docs = ContextVar("healthnews_grade_docs", default=())


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def validate_snapshot(snapshot, live_collection, live_table):
    required = ("snapshot_id", "mongo_collection", "pg_table", "mongo_search_index", "mongo_sha256", "pg_sha256")
    if snapshot.get("status") != "verified_immutable" or any(not snapshot.get(k) for k in required):
        raise ValueError("formal execution requires complete verified snapshot coordinates and hashes")
    if snapshot["mongo_collection"] == live_collection or snapshot["pg_table"] == live_table:
        raise ValueError("formal snapshot cannot point to the live collection or table")
    if any(not re.fullmatch(r"[0-9a-f]{64}", snapshot[k]) for k in ("mongo_sha256", "pg_sha256")):
        raise ValueError("snapshot content hashes must be SHA-256")
    if any(not isinstance(snapshot.get(k), int) or snapshot[k] <= 0 for k in ("mongo_count", "pg_count")):
        raise ValueError("snapshot counts must be positive integers")
    return snapshot


def current():
    row = _record.get()
    if row is None:
        raise RuntimeError("experiment event outside a run")
    return row


def evidence_ids(docs):
    row, ids = current(), []
    for doc in docs:
        text_hash = hashlib.sha256(doc.page_content.encode()).hexdigest()
        chunk_id = str(doc.metadata.get("id") or text_hash)
        key = f"{row['corpus_ref']}:{chunk_id}:{text_hash[:12]}"
        ids.append(key)
        row["_evidence"].setdefault(key, {"evidence_id": key, "chunk_id": chunk_id,
            "text": doc.page_content, "text_hash": text_hash, "metadata": doc.metadata,
            "snapshot_id": row["snapshot_id"]})
    return ids


@asynccontextmanager
async def observed(kind, **fields):
    row = current()
    e = {"event_id": f"e{len(row['events']) + 1}", "kind": kind,
         "parent_event_id": _parent.get(), "status": "started", **fields}
    row["events"].append(e)
    task = asyncio.current_task()
    row["_tasks"][task] = row["_tasks"].get(task, 0) + 1
    token = _parent.set(e["event_id"])
    start = time.perf_counter()
    try:
        yield e
        e["status"] = "completed"
    except asyncio.CancelledError:
        e["status"] = "cancelled"
        raise
    except Exception as exc:
        e["status"] = "error"
        # Exceptions may include URLs with API keys; never store unsanitized strings.
        e["error_type"] = type(exc).__name__
        e["http_status"] = getattr(getattr(exc, "response", None), "status_code", None)
        row["runtime_errors"].append({"event_id": e["event_id"],
                                     "error_type": e["error_type"], "http_status": e["http_status"]})
        raise
    finally:
        e["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
        row["_tasks"][task] -= 1
        if row["_tasks"][task] == 0:
            del row["_tasks"][task]
        _parent.reset(token)


class ObservedModel:
    def __init__(self, model, role, model_name=None):
        self.model, self.role, self.model_name = model, role, model_name

    def with_structured_output(self, *args, **kwargs):
        return ObservedModel(self.model.with_structured_output(*args, **kwargs), self.role, self.model_name)

    async def ainvoke(self, messages, *args, **kwargs):
        inputs = [{"role": m.type, "content": m.content} for m in messages]
        async with observed("llm", role=self.role, model=self.model_name,
                            effective_input=inputs, prompt_hash=digest(inputs)) as e:
            result = await self.model.ainvoke(messages, *args, **kwargs)
            e["output"] = content_to_text(result.content) if hasattr(result, "content") else result
            e["usage_metadata"] = getattr(result, "usage_metadata", None)
            e["response_metadata"] = getattr(result, "response_metadata", None)
            return result


class ObservedRetriever:
    def __init__(self, retriever, layer):
        self.retriever, self.layer = retriever, layer

    async def ainvoke(self, query):
        async with observed("retrieval", layer=self.layer, query=query) as e:
            docs = await self.retriever.ainvoke(query)
            e["included_evidence_ids"] = evidence_ids(docs)
            e["candidate_count"] = len(docs)
            e["candidates"] = [{"evidence_id": key, "rank": i, "metadata": doc.metadata}
                for i, (key, doc) in enumerate(zip(e["included_evidence_ids"], docs), 1)]
            return docs


class ObservedDecomposer:
    def __init__(self, decomposer):
        self.decomposer = decomposer

    async def decompose(self, message):
        row = current()
        row["decomposer_called"] = True
        async with observed("decomposition", original_message=message) as e:
            subs = await self.decomposer.decompose(message)
            row["actual_subquestions"] = e["subquestions"] = [asdict(s) for s in subs]
            row["actual_decomposition_triggered"] = len(subs) >= 2
            return subs


class ObservedGrader:
    def __init__(self, grader):
        self.grader = grader

    async def grade(self, query, docs):
        async with observed("crag", query_actually_graded=query, included_evidence_ids=evidence_ids(docs),
                            expected_crag=None, independent_review_status="pending", model_reason=None) as e:
            grade = await self.grader.grade(query, docs)
            e["actual_crag"] = grade.value
            return grade


class ObservedGeminiGrader(GeminiRetrievalGrader):
    async def grade(self, query, docs):
        token = _grade_docs.set(docs)
        try:
            return await super().grade(query, docs)
        finally:
            _grade_docs.reset(token)

    async def _call(self, prompt):
        async with observed("grader_provider", provider="Gemini", effective_input=prompt,
                            prompt_hash=digest(prompt), max_chars_per_doc=self._max_chars,
                            included_evidence_ids=evidence_ids(_grade_docs.get())) as e:
            e["output"] = await super()._call(prompt)
            return e["output"]


class ObservedJevGrader(JevRetrievalGrader):
    async def grade(self, query, docs):
        # A cancelled provider call followed by a successful fallback was a timeout;
        # cancellation of the entire CARE request propagates and never reaches here.
        start_index = len(current()["events"])
        parent_id = _parent.get()
        grade = await super().grade(query, docs)
        for e in current()["events"][start_index:]:
            if (e["kind"] == "grader_provider" and e["provider"] == "Jev" and e["status"] == "cancelled"
                    and e["parent_event_id"] == parent_id):
                e["status"] = "timeout"
                current()["runtime_errors"].append({"event_id": e["event_id"], "error_type": "TimeoutError",
                                                   "provider": "Jev", "fallback": "Gemini"})
        return grade

    async def _ask(self, query, docs):
        payload = {"state": build_state(query, docs), "model": JEV_MODEL, "questions": QUESTION}
        async with observed("grader_provider", provider="Jev", model=JEV_MODEL,
                            effective_input=payload, prompt_hash=digest(payload), prompt_template_hash=digest(QUESTION), max_chars_per_doc=400,
                            included_evidence_ids=evidence_ids(docs)) as e:
            e["probabilities"] = await super()._ask(query, docs)
            return e["probabilities"]


class ObservedRagAnswerService(RagAnswerService):
    async def _generate_answer(self, question, docs, *, speculative=False):
        context = self._build_context(docs)
        async with observed("generation", question=question, included_evidence_ids=evidence_ids(docs),
                            context=context, context_hash=digest(context), context_characters=len(context),
                            context_tokens=None, speculative=speculative, adopted=False) as e:
            e["raw_answer"] = await super()._generate_answer(question, docs, speculative=speculative)
            return e["raw_answer"]

    def _append_sources(self, answer_text, docs, dead_urls=frozenset()):
        row = current()
        ids = evidence_ids(docs)
        row["generation_evidence_ids"] = ids
        row["citation_mapping_before_renumber"] = {
            str(i): ids[i - 1] for i in cited_indices(answer_text) if 1 <= i <= len(ids)}
        for e in reversed(row["events"]):
            if e["kind"] == "generation" and e["status"] == "completed" and e["included_evidence_ids"] == ids:
                e["adopted"] = True
                break
        return super()._append_sources(answer_text, docs, dead_urls)


class Capture(logging.Handler):
    def emit(self, record):
        row = _record.get()
        if row is None:
            return
        message = record.getMessage()
        if message.startswith("stage="):
            row["stages"].append(dict(re.findall(r"(\w+)=(\S+)", message)))
        elif record.levelno >= logging.WARNING:
            row["warnings"].append({"logger": record.name, "level": record.levelname,
                "message_tag": message.split()[0] if message else ""})


async def run_case(case, arm, round_number, service, corpus_ref, *, snapshot_id=None, phase="pilot"):
    if service.web_fallback_enabled or service.web_search is not None:
        raise ValueError("experiment requires disabled web fallback and no web service")
    question = case["question"]
    row = {"case_id": case["id"], "arm": arm, "round": round_number, "phase": phase,
        "original_question": question, "original_message": question,
        "started_at": datetime.now(timezone.utc).isoformat(), "snapshot_id": snapshot_id,
        "corpus_ref": corpus_ref, "run_status": "started", "decomposition_enabled": service.decomposer is not None,
        "decomposer_called": False, "actual_decomposition_triggered": False, "actual_subquestions": [],
        "web_fallback_executed": False, "answer_cache_policy": "no_answer_cache", "automatic_writeback": False,
        "events": [], "runtime_errors": [], "warnings": [], "stages": [], "generation_evidence_ids": [],
        "care_output": None, "review_status": "pending", "_evidence": {}, "_tasks": {}}
    token = _record.set(row)
    language = set_request_language(case.get("language", "zh-TW"))
    sources = begin_request_rag_sources()
    start = time.perf_counter()
    try:
        try:
            row["care_output"] = await service.answer(question, original_message=question)
            row["run_status"] = "completed"
        except Exception as exc:
            row["run_status"] = "runtime_error"
            if not row["runtime_errors"]:
                row["runtime_errors"].append({"error_type": type(exc).__name__})
        row["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
        pending = set(row["_tasks"]) - {asyncio.current_task()}
        if pending:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        row.pop("_tasks")
        row["sources"] = [asdict(s) for s in get_request_rag_sources()]
        row["evidence"] = list(row.pop("_evidence").values())
        main = [e for e in row["events"] if e["kind"] == "retrieval" and e["layer"] == "hybrid"]
        row["retrieval_calls"] = len(main)
        row["candidate_count_total"] = sum(e.get("candidate_count", 0) for e in main)
        row["retrieval_leg_candidate_count_total"] = sum(e.get("candidate_count", 0) for e in row["events"]
            if e["kind"] == "retrieval" and e["layer"] != "hybrid")
        row["fail_code"] = parse_rag_fail_code(row["care_output"] or "")
        if row["fail_code"] and row["fail_code"] not in ("KB_EMPTY", "MODEL_REFUSE"):
            row["run_status"] = "runtime_error"
        elif row["runtime_errors"] or row["warnings"]:
            row["run_status"] = "completed_with_incidents" if row["care_output"] else "runtime_error"
        notes = [row["care_output"]] if is_rag_fail(row["care_output"] or "") else []
        for sub in row["actual_subquestions"]:
            note = t("rag.compound_unsupported").format(question=sub["question"])
            if note in (row["care_output"] or ""):
                notes.append(note)
        row["insufficiency"] = {"program_template_text": notes,
            "origin": "program_template" if notes else "pending_review", "llm_text_review_status": "pending"}
        row["completed_at"] = datetime.now(timezone.utc).isoformat()
        return row
    finally:
        reset_request_rag_sources(sources)
        reset_request_language(language)
        _record.reset(token)


def build_components(snapshot):
    from app.core.config import settings as s
    from app.services.gemini import GeminiService
    from app.services.rag.cohere_reranker import CohereReranker, _default_http_post
    from app.services.rag.link_check import LinkChecker
    from app.services.rag.pgvector_retriever import PgVectorRetriever
    from app.services.rag.question_decomposer import GeminiQuestionDecomposer
    from app.services.rag.query_rewriter import GeminiQueryRewriter, REWRITE_THINKING_LEVEL
    from app.services.rag.retrieval_grader import GRADE_THINKING_LEVEL
    from app.services.rag.retriever import HybridRetriever, MongoAtlasTextRetriever
    from langchain_google_genai import GoogleGenerativeAIEmbeddings

    required = [k for k in ("GEMINI_API_KEY", "COHERE_API_KEY", "PGVECTOR_DSN", "MONGODB_URI", "MONGODB_TEXT_INDEX") if not getattr(s, k)]
    if required or not s.RAG_HYBRID_ENABLED or not s.RAG_CRAG_ENABLED:
        raise ValueError("missing experiment configuration: " + ",".join(required))
    collection = snapshot.get("mongo_collection", s.MONGODB_COLLECTION)
    table = snapshot.get("pg_table", s.PGVECTOR_TABLE)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", table):
        raise ValueError("unsafe snapshot table identifier")
    embedding = GoogleGenerativeAIEmbeddings(model=s.EMBEDDING_MODEL, google_api_key=s.GEMINI_API_KEY,
        task_type="RETRIEVAL_QUERY", output_dimensionality=s.MONGODB_VECTOR_DIM)
    vector = PgVectorRetriever(embeddings=embedding, dsn=s.PGVECTOR_DSN, mongo_uri=s.MONGODB_URI,
        db_name=s.MONGODB_DB, collection_name=collection, table_name=table,
        vector_column=s.PGVECTOR_VECTOR_COLUMN, text_field=s.MONGODB_TEXT_FIELD, vector_dim=s.MONGODB_VECTOR_DIM,
        k=s.RAG_RETRIEVE_CANDIDATES, min_score=s.RAG_VECTOR_MIN_SCORE)
    text = MongoAtlasTextRetriever(mongo_uri=s.MONGODB_URI, db_name=s.MONGODB_DB, collection_name=collection,
        index_name=snapshot.get("mongo_search_index", s.MONGODB_TEXT_INDEX), text_field=s.MONGODB_TEXT_FIELD,
        title_field=s.MONGODB_TEXT_TITLE_FIELD, title_boost=s.RAG_TEXT_TITLE_BOOST, k=s.RAG_RETRIEVE_CANDIDATES)
    hybrid = HybridRetriever(vector_retriever=ObservedRetriever(vector, "vector"), text_retriever=ObservedRetriever(text, "bm25"),
        rrf_k=s.RAG_RRF_K, limit=s.RAG_RETRIEVE_CANDIDATES, fusion_mode=s.RAG_FUSION_MODE, alpha=s.RAG_FUSION_ALPHA,
        leg_timeout_seconds=s.RAG_RETRIEVE_LEG_TIMEOUT_SECONDS)

    async def cohere_post(url, *, headers, json, timeout):
        async with observed("rerank_provider", provider="Cohere", effective_input=json, prompt_hash=digest(json)) as e:
            e["output"] = await _default_http_post(url, headers=headers, json=json, timeout=timeout)
            return e["output"]

    def gemini(model, role, thinking=None):
        instance = GeminiService(api_key=s.GEMINI_API_KEY, model_name=model, thinking_level=thinking)
        return SimpleNamespace(chat_model=ObservedModel(instance.chat_model, role, model))

    grader = ObservedGrader(ObservedJevGrader(api_key=s.TYPESAFE_API_KEY, fallback=ObservedGeminiGrader(
        gemini_service=gemini(s.MODEL_NAME, "crag_gemini", GRADE_THINKING_LEVEL))))
    generate = gemini(s.RAG_GENERATE_MODEL_NAME, "generation")
    rewrite = GeminiQueryRewriter(gemini_service=gemini(s.MODEL_NAME, "rewrite", REWRITE_THINKING_LEVEL))
    decomposer = ObservedDecomposer(GeminiQuestionDecomposer(gemini(s.MODEL_NAME, "decomposition", REWRITE_THINKING_LEVEL)))
    reranker = CohereReranker(api_key=s.COHERE_API_KEY, model=s.COHERE_RERANK_MODEL,
        timeout_seconds=s.COHERE_RERANK_TIMEOUT_SECONDS, http_post=cohere_post)

    def new_service(arm):
        checker = LinkChecker(timeout_seconds=s.RAG_LINK_CHECK_TIMEOUT_SECONDS, ok_ttl_seconds=s.RAG_LINK_CHECK_OK_TTL_SECONDS,
            dead_ttl_seconds=s.RAG_LINK_CHECK_DEAD_TTL_SECONDS) if s.RAG_LINK_CHECK_ENABLED else None
        return ObservedRagAnswerService(gemini_service=generate, retriever=ObservedRetriever(hybrid, "hybrid"),
            reranker=reranker, rerank_top_n=s.RAG_RERANK_TOP_N, max_chunks_per_article=s.RAG_RERANK_MAX_CHUNKS_PER_ARTICLE,
            grader=grader, rewriter=rewrite, crag_enabled=True, web_search=None, web_fallback_enabled=False,
            degraded_min_score=s.RAG_DEGRADED_MIN_SCORE, link_checker=checker, speculative_generate=s.RAG_SPECULATIVE_GENERATE,
            total_timeout_seconds=s.RAG_ANSWER_TIMEOUT_SECONDS, decomposer=decomposer if arm == "D" else None)
    return new_service, vector, generate.chat_model.model


async def verify_snapshot(vector, snapshot):
    """正式執行前後都讀回實體資料，不能只相信 manifest 的狀態字串。"""
    mongo_hash, pg_hash = hashlib.sha256(), hashlib.sha256()
    mongo_ids, pg_ids = [], []
    collection = vector._ensure_collection()
    async for doc in collection.find({}).sort("_id", 1):
        mongo_hash.update(json.dumps(doc, ensure_ascii=False, sort_keys=True, default=str).encode() + b"\n")
        mongo_ids.append(str(doc["_id"]))
    pool = await vector._ensure_pool()
    async with pool.acquire() as conn:
        async with conn.transaction(readonly=True, isolation="repeatable_read"):
            query = f"SELECT id, {vector.vector_column}::text AS embedding FROM {vector.table_name} ORDER BY id"
            async for row in conn.cursor(query):
                pg_hash.update(json.dumps([str(row["id"]), row["embedding"]], ensure_ascii=False).encode() + b"\n")
                pg_ids.append(str(row["id"]))
    actual = {"mongo_sha256": mongo_hash.hexdigest(), "pg_sha256": pg_hash.hexdigest(),
              "mongo_count": len(mongo_ids), "pg_count": len(pg_ids)}
    if any(actual[k] != snapshot[k] for k in actual) or mongo_ids != pg_ids:
        raise ValueError("physical snapshot does not match its manifest or PG/Mongo IDs differ")
    indexes = await collection.list_search_indexes(snapshot["mongo_search_index"]).to_list(None)
    if not indexes or not all(i.get("queryable") for i in indexes):
        raise ValueError("snapshot search index is not queryable")
    return actual


async def main(args):
    from app.core.config import settings
    dataset = json.loads(args.dataset.read_text())
    cases = [{k: c[k] for k in ("id", "question", "language") if k in c} for c in dataset["cases"]]
    if args.ids:
        if set(args.ids) - {c["id"] for c in cases}:
            raise ValueError("unknown case IDs")
        cases = [c for c in cases if c["id"] in args.ids]
    snapshot = json.loads(args.snapshot.read_text()) if args.snapshot else {}
    if args.phase == "formal":
        validate_snapshot(snapshot, settings.MONGODB_COLLECTION, settings.PGVECTOR_TABLE)
    args.out.mkdir(parents=True, exist_ok=True)
    results = args.out / "runs.jsonl"
    if results.exists():
        raise ValueError("use a new output directory; existing rounds cannot be overwritten")
    factory, vector, model = build_components(snapshot)
    verified_before = await verify_snapshot(vector, snapshot) if args.phase == "formal" else None
    config = {k: getattr(settings, k) for k in dir(settings) if k.startswith(("RAG_", "MONGODB_", "PGVECTOR_", "COHERE_"))
        and not any(s in k for s in ("KEY", "URI", "DSN", "TOKEN", "SECRET"))}
    stamp = datetime.now(timezone.utc).isoformat()
    corpus_ref = snapshot.get("snapshot_id") or "live_readonly_" + stamp
    manifest = {"phase": args.phase, "created_at": stamp, "dataset_sha256": hashlib.sha256(args.dataset.read_bytes()).hexdigest(),
        "model_input_fields": ["question", "language"], "case_ids": [c["id"] for c in cases], "arms": ["B", "D"],
        "blocked_arms": {"A": "missing_old_snapshot", "C": "missing_old_snapshot"}, "rounds": args.rounds,
        "planned_runs": len(cases) * 2 * args.rounds, "settings": config,
        "snapshot": snapshot or {"snapshot_id": None, "status": "live_pilot_not_formal"},
        "models": {k: getattr(settings, k) for k in ("MODEL_NAME", "RAG_GENERATE_MODEL_NAME", "EMBEDDING_MODEL")},
        "source_file_hashes": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (ROOT / "app/services/rag").glob("*.py")},
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "snapshot_verified_before": verified_before, "concurrency": 1,
        "link_cache": "new checker per case run", "answer_cache": "none", "writeback": False,
        "web_fallback_enabled": False, "score_computed": False, "question_review_status": "pending_human_review",
        "generation_budget": {"max_evidence_chunks": settings.RAG_RERANK_TOP_N, "context_token_cap": None,
                              "status": "no_explicit_token_cap_in_CARE_reported_deviation"}}
    (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    capture = Capture()
    logging.getLogger().addHandler(capture)
    logging.getLogger().setLevel(logging.INFO)
    try:
        with results.open("x") as stream:
            count = 0
            for round_number in range(1, args.rounds + 1):
                for index, case in enumerate(cases):
                    arms = ["B", "D"] if (index + round_number) % 2 else ["D", "B"]
                    for arm in arms:
                        row = await run_case(case, arm, round_number, factory(arm), corpus_ref,
                            snapshot_id=snapshot.get("snapshot_id"), phase=args.phase)
                        # Count context separately, outside CARE latency, and record extra resource calls.
                        resource_calls = []
                        for e in row["events"]:
                            if e["kind"] == "generation":
                                resource = {"kind": "context_count_tokens", "generation_event_id": e["event_id"]}
                                resource_start = time.perf_counter()
                                try:
                                    from app.services.rag.answer_prompts import wrap_context
                                    # Use an awaitable SDK request: cancellation must cancel the request,
                                    # rather than leave a synchronous count_tokens worker running.
                                    from google import genai
                                    async with genai.Client(api_key=settings.GEMINI_API_KEY).aio as counter:
                                        counted = await asyncio.wait_for(counter.models.count_tokens(
                                            model=settings.RAG_GENERATE_MODEL_NAME,
                                            contents=wrap_context(e["context"])), 15)
                                    e["context_tokens"] = counted.total_tokens
                                    e["context_token_method"] = "provider_count_tokens"
                                    resource["status"] = "completed"
                                except Exception as exc:
                                    e["context_token_error"] = type(exc).__name__
                                    resource.update(status="error", error_type=type(exc).__name__)
                                resource["latency_ms"] = round((time.perf_counter() - resource_start) * 1000, 2)
                                resource_calls.append(resource)
                        row["experiment_resource_calls"] = resource_calls
                        stream.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                        stream.flush()
                        count += 1
                        print(f"{count}/{manifest['planned_runs']} {case['id']} {arm} r{round_number} "
                              f"{row['run_status']} subs={len(row['actual_subquestions'])} "
                              f"retrieval={row['retrieval_calls']} ms={row['latency_ms']}", flush=True)
        if args.phase == "formal":
            manifest["snapshot_verified_after"] = await verify_snapshot(vector, snapshot)
        manifest["completed_runs"] = count
        manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
        (args.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    finally:
        logging.getLogger().removeHandler(capture)
        if vector._pool is not None:
            await vector._pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--phase", choices=("pilot", "formal"), default="pilot")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--rounds", type=int, choices=(1, 2, 3), default=3)
    asyncio.run(main(parser.parse_args()))
