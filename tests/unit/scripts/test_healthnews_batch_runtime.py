"""真實 RAG 與持久 Batch 協同；fake 僅替代外部 API／DB。"""
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

from langchain_core.documents import Document
from langchain_core.messages import HumanMessage
import pytest

from tests.unit.scripts.test_healthnews_batch_config import fixture
from scripts.healthnews_batch.store import Journal
from scripts.healthnews_batch.transport import BatchCoordinator


class Provider:
    def __init__(self):
        self.jobs, self.inputs, self.creates = {}, [], 0

    async def create(self, model, requests, display_name):
        self.creates += 1
        name = f"batches/{self.creates}"
        self.jobs[name] = requests
        self.inputs.extend(requests)
        return {"name": name, "state": "JOB_STATE_PENDING"}

    async def get(self, name):
        responses = []
        for request in self.jobs[name]:
            text = "\n".join(p["text"] for c in request["contents"] for p in c["parts"])
            if "問題分析器" in text:
                output = json.dumps({"strategy": "compound", "sub_questions": [
                    {"question": "評估什麼？", "retrieval_query": "評估"},
                    {"question": "治療相同嗎？", "retrieval_query": "治療"}]}, ensure_ascii=False)
            elif request.get("config", {}).get("response_json_schema"):
                output = json.dumps({"kb_query": "評估", "zh_terms": "評估", "en_terms": "evaluation", "typo_from": "", "typo_to": ""})
            else:
                output = "依證據回答 [1]。"
            responses.append({"metadata": request["metadata"], "response": {
                "candidates": [{"content": {"parts": [{"text": output}]}}],
                "usage_metadata": {"total_token_count": 10}, "model_version": "fake-version"}})
        return {"state": "JOB_STATE_SUCCEEDED", "responses": list(reversed(responses))}


class Retriever:
    async def ainvoke(self, query):
        return [Document(page_content="取回的證據", metadata={"id": "chunk-1", "score": 0.9,
                         "source_name": "測試", "url": "https://example.org/1"})]


async def correct(prompt):
    return {"grade": "correct"}


async def token_count(context):
    return 12


def factory(coordinator, journal, *, counter=token_count):
    from scripts.healthnews_batch.runtime import BatchModel, DurableCalls, OfflineRagService, ReceiptRetriever
    from scripts.healthnews_experiment import ObservedModel, ObservedRetriever, ObservedGrader, ObservedGeminiGrader, ObservedDecomposer
    from app.services.rag.cohere_reranker import VectorScoreReranker
    from app.services.rag.question_decomposer import GeminiQuestionDecomposer

    def new_service(arm):
        calls = DurableCalls(journal)
        model = ObservedModel(BatchModel(coordinator, "generation", "generation-model"), "generation", "generation-model")
        decomposer = ObservedDecomposer(GeminiQuestionDecomposer(SimpleNamespace(
            chat_model=ObservedModel(BatchModel(coordinator, "decomposition", "analysis-model", "low"), "decomposition", "analysis-model"))))
        return OfflineRagService(gemini_service=SimpleNamespace(chat_model=model),
            retriever=ObservedRetriever(ReceiptRetriever(Retriever(), calls, "hybrid"), "hybrid"),
            reranker=VectorScoreReranker(), grader=ObservedGrader(ObservedGeminiGrader(invoke_grade=correct)),
            crag_enabled=True, web_fallback_enabled=False, speculative_generate=True,
            total_timeout_seconds=0, decomposer=decomposer if arm == "D" else None,
            calls=calls, token_counter=counter, candidate_budget=160, context_token_budget=16384)
    return new_service


async def test_six_independent_real_rag_runs_and_resume_without_resubmit(tmp_path):
    from scripts.healthnews_batch.config import load_config
    from scripts.healthnews_batch.runtime import run_experiment
    path, _, root = fixture(tmp_path)
    config = load_config(path, code_root=root)
    config = replace(config, blockers=[], snapshots={"new": {"snapshot_id": "snapshot-1"}},
                     cases=[{"id": "HN001", "question": "評估什麼，治療相同嗎？", "language": "zh-TW"}])
    journal = Journal(tmp_path / "journal.sqlite")
    provider = Provider()
    coordinator = BatchCoordinator(journal, provider, config.version, poll_interval=0)
    rows = await run_experiment(config, journal, coordinator, factory(coordinator, journal))
    assert len(rows) == 6
    assert {(r["arm"], r["round"]) for r in rows} == {(a, n) for a in ("B", "D") for n in (1, 2, 3)}
    assert all(r["care_output"].startswith("依證據回答") for r in rows)
    assert all(r["original_message"] == "評估什麼，治療相同嗎？" for r in rows)
    assert all(r["actual_decomposition_triggered"] == (r["arm"] == "D") for r in rows)
    assert len({r["config_version"] for r in rows}) == 1
    assert all(r["subquestion_reviews"] and r["review_status"] == "pending" for r in rows)
    assert "不能輸入模型" not in json.dumps(provider.inputs, ensure_ascii=False)
    initial = provider.creates
    assert await run_experiment(config, journal, coordinator, factory(coordinator, journal)) == rows
    assert provider.creates == initial


async def test_local_receipt_replays_same_run_only(tmp_path):
    from scripts.healthnews_batch.runtime import DurableCalls, run_scope
    journal = Journal(tmp_path / "j.sqlite")
    calls = DurableCalls(journal)
    state = {"value": 0}

    async def outside():
        state["value"] += 1
        return {"external_value": state["value"]}

    with run_scope("round1"):
        assert await calls.call("retrieval", {"query": "評估"}, outside) == {"external_value": 1}
    with run_scope("round1"):
        assert await calls.call("retrieval", {"query": "評估"}, outside) == {"external_value": 1}
    with run_scope("round2"):
        assert await calls.call("retrieval", {"query": "評估"}, outside) == {"external_value": 2}


async def test_structured_output_uses_actual_prompt_and_schema(tmp_path):
    from scripts.healthnews_batch.runtime import BatchModel, run_scope
    journal = Journal(tmp_path / "j.sqlite")
    provider = Provider()
    coordinator = BatchCoordinator(journal, provider, "v1", poll_interval=0)

    async def request():
        with run_scope("HN001:D:r1"):
            model = BatchModel(coordinator, "rewrite", "gemini-3.8-flash", "low").with_structured_output(
                {"type": "object", "properties": {"kb_query": {"type": "string"}}}, method="json_schema")
            return await model.ainvoke([HumanMessage(content="原始改寫 prompt")])
    result = (await coordinator.drive([asyncio.create_task(request())]))[0]
    assert result["kb_query"] == "評估"
    assert provider.inputs[0]["contents"][0]["parts"][0]["text"] == "原始改寫 prompt"
    assert provider.inputs[0]["config"]["thinking_config"] == {"thinking_level": "low"}


async def test_context_budget_failure_prevents_generation_submission(tmp_path):
    from scripts.healthnews_batch.config import load_config
    from scripts.healthnews_batch.runtime import run_experiment
    path, _, root = fixture(tmp_path)
    config = replace(load_config(path, code_root=root), blockers=[], snapshots={"new": {"snapshot_id": "snapshot-1"}})
    journal = Journal(tmp_path / "j.sqlite")
    provider = Provider()
    coordinator = BatchCoordinator(journal, provider, config.version, poll_interval=0)

    async def exceeded(context):
        return 20000

    rows = await run_experiment(config, journal, coordinator, factory(coordinator, journal, counter=exceeded))
    assert all(r["run_status"] == "runtime_error" for r in rows)
    assert all("budget_exceeded" in r["blocking_findings"] for r in rows)
    assert not any("請根據以下" in json.dumps(r, ensure_ascii=False) for r in provider.inputs)


async def test_source_link_receipt_preserves_actual_alive_contract(tmp_path):
    from scripts.healthnews_batch.runtime import DurableCalls, ReceiptLinkChecker, run_scope
    from app.services.rag.link_check import dead_urls
    class Checker:
        async def alive(self, urls):
            return {url: url.endswith("alive") for url in urls}
    calls = DurableCalls(Journal(tmp_path / "j.sqlite"))
    checker = ReceiptLinkChecker(Checker(), calls)
    with run_scope("r1"):
        result = await dead_urls(checker, ["https://example.org/alive", "https://example.org/dead"])
    assert result == frozenset({"https://example.org/dead"})


async def test_local_interruption_is_unknown_and_prevents_next_external_call(tmp_path):
    from scripts.healthnews_batch.runtime import DurableCalls, run_scope
    journal = Journal(tmp_path / "j.sqlite")
    calls = DurableCalls(journal)
    entered = asyncio.Event()
    async def outside():
        entered.set()
        await asyncio.Event().wait()
    async def operation():
        with run_scope("r1"):
            return await calls.call("cohere", {"query": "評估"}, outside)
    task = asyncio.create_task(operation())
    await entered.wait()
    assert journal.db.execute("SELECT status FROM receipts").fetchone()[0] == "started"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert journal.db.execute("SELECT status FROM receipts").fetchone()[0] == "unknown"
    with run_scope("r2"):
        with pytest.raises(ValueError, match="[Uu]ncertain"):
            await calls.call("cohere", {"query": "another"}, outside)


async def test_cancelled_read_only_token_count_can_retry_without_poisoning_run(tmp_path):
    from scripts.healthnews_batch.runtime import DurableCalls, run_scope
    journal = Journal(tmp_path / "j.sqlite")
    calls = DurableCalls(journal)
    entered = asyncio.Event()
    async def counting():
        entered.set()
        await asyncio.Event().wait()
    async def operation():
        with run_scope("r1"):
            return await calls.call("context_count_tokens", {"context": "證據"}, counting)
    task = asyncio.create_task(operation())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    journal.assert_no_uncertain_receipts()
    async def resolved():
        return 12
    with run_scope("r1"):
        assert await calls.call("context_count_tokens", {"context": "證據"}, resolved) == 12


async def test_actual_provider_timeout_is_recorded_and_same_run_replays_error(tmp_path):
    from scripts.healthnews_batch.runtime import DurableCalls, run_scope
    journal = Journal(tmp_path / "j.sqlite")
    calls = DurableCalls(journal)
    async def outside():
        await asyncio.Event().wait()
    with run_scope("r1"):
        with pytest.raises(TimeoutError):
            await calls.call("jev", {"query": "評估"}, outside, timeout_seconds=0.001)

    assert journal.db.execute("SELECT status FROM receipts").fetchone()[0] == "error"
    journal.assert_no_uncertain_receipts()
    with run_scope("r1"):
        with pytest.raises(TimeoutError, match="recorded"):
            await calls.call("jev", {"query": "評估"}, outside, timeout_seconds=0.001)


async def test_owner_close_cancels_pending_read_before_journal_closes(tmp_path):
    from scripts.healthnews_batch.runtime import DurableCalls, run_scope
    journal = Journal(tmp_path / "j.sqlite")
    calls = DurableCalls(journal)
    entered = asyncio.Event()
    async def outside():
        entered.set()
        await asyncio.Event().wait()
    async def pending():
        with run_scope("r1"):
            return await calls.call("source_link_check", {"urls": ["https://example.org/1"]}, outside)
    task = asyncio.create_task(pending())
    await entered.wait()
    try:
        await calls.close()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()
    assert journal.db.execute("SELECT status FROM receipts").fetchone()[0] == "retryable_read"
    journal.close()


async def test_live_rag_still_uses_batch_and_never_claims_fixed_snapshot(tmp_path):
    from tests.unit.scripts.test_healthnews_batch_config import live_fixture
    from scripts.healthnews_batch.config import load_config
    from scripts.healthnews_batch.runtime import run_experiment
    path, _, root=live_fixture(tmp_path)
    config=load_config(path,code_root=root)
    config=replace(config,cases=[{'id':'HN001','question':'評估什麼，治療相同嗎？','language':'zh-TW'}])
    journal=Journal(tmp_path/'journal.sqlite');provider=Provider()
    coordinator=BatchCoordinator(journal,provider,config.version,poll_interval=0)
    rows=await run_experiment(config,journal,coordinator,factory(coordinator,journal))
    assert provider.creates>0 and len(rows)==6
    assert all(r['phase']=='live_observed' and r['immutable_corpus'] is False for r in rows)
    assert not any(r['web_fallback_executed'] for r in rows)
    created=provider.creates
    await run_experiment(config,journal,coordinator,factory(coordinator,journal))
    assert provider.creates==created
    journal.close()
