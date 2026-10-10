"""實驗入口的原文隔離、有效證據與錯誤紀錄契約。"""

import asyncio
import hashlib
from types import SimpleNamespace

import pytest

from langchain_core.documents import Document
from langchain_core.messages import AIMessage

from app.services.rag.cohere_reranker import VectorScoreReranker
from app.services.rag.question_decomposer import SubQuestion
from scripts.healthnews_experiment import (
    ObservedDecomposer,
    ObservedGeminiGrader,
    ObservedGrader,
    ObservedModel,
    ObservedJevGrader,
    ObservedRagAnswerService,
    ObservedRetriever,
    run_case,
    validate_snapshot,
    verify_snapshot,
)


class Model:
    def __init__(self):
        self.prompts = []

    async def ainvoke(self, messages):
        self.prompts.append("\n".join(m.content for m in messages))
        return AIMessage(content="依資料回答 [1]。")


class Retriever:
    async def ainvoke(self, query):
        return [Document(page_content="甲" * 450 + "不能省略的尾段", metadata={
            "id": "chunk-1", "original_title": "標題", "source_name": "來源",
            "url": "https://example.org/article", "score": 0.9,
        })]


class Decomposer:
    async def decompose(self, message):
        return [SubQuestion("評估什麼？", "評估"), SubQuestion("治療相同嗎？", "治療")]


async def correct(prompt):
    return {"grade": "correct"}


def service(*, decompose=False, retriever=None, speculative=False, model=None):
    return ObservedRagAnswerService(
        gemini_service=SimpleNamespace(chat_model=ObservedModel(model or Model(), "generation")),
        retriever=ObservedRetriever(retriever or Retriever(), "hybrid"),
        reranker=VectorScoreReranker(),
        grader=ObservedGrader(ObservedGeminiGrader(invoke_grade=correct)),
        crag_enabled=True, speculative_generate=speculative,
        web_fallback_enabled=False,
        decomposer=ObservedDecomposer(Decomposer()) if decompose else None,
    )


async def test_run_uses_only_original_question_and_records_actual_decomposition():
    model = Model()
    row = await run_case({"id": "HN066", "question": "評估什麼，治療相同嗎？",
                          "reference_answer": "禁止送入模型的答案",
                          "reference_subquestions": [{"query": "禁止送入模型的子題"}]},
                         "D", 1, service(decompose=True, model=model), "snapshot-1")

    assert row["decomposer_called"] is True
    assert row["actual_decomposition_triggered"] is True
    assert row["actual_subquestions"][0]["question"] == "評估什麼？"
    assert row["original_message"] == "評估什麼，治療相同嗎？"
    assert all("禁止送入模型" not in prompt for prompt in model.prompts)
    assert row["retrieval_calls"] == 2
    assert row["care_output"].startswith("依資料回答")


async def test_grader_effective_input_is_truncated_but_full_evidence_is_preserved():
    row = await run_case({"id": "HN001", "question": "能否回答？"}, "B", 1, service(), "snapshot-1")

    event = next(e for e in row["events"] if e["kind"] == "grader_provider" and e["provider"] == "Gemini")
    assert "甲" * 400 in event["effective_input"]
    assert "不能省略的尾段" not in event["effective_input"]
    assert "不能省略的尾段" in row["evidence"][0]["text"]
    assert event["included_evidence_ids"] == [row["evidence"][0]["evidence_id"]]
    assert row["generation_evidence_ids"] == [row["evidence"][0]["evidence_id"]]


async def test_retrieval_api_error_is_not_reported_as_empty_database():
    class Broken:
        async def ainvoke(self, query):
            raise ConnectionError("retrieval unavailable")

    row = await run_case({"id": "HN001", "question": "能否回答？"}, "B", 1,
                         service(retriever=Broken()), "snapshot-1")

    assert row["run_status"] == "runtime_error"
    assert row["runtime_errors"][0]["error_type"] == "ConnectionError"
    assert row["care_output"] is None


async def test_cancelled_speculative_generation_is_retained_and_not_adopted():
    class Slow:
        async def ainvoke(self, messages):
            await asyncio.sleep(10)

    async def incorrect(prompt):
        await asyncio.sleep(0)
        return {"grade": "incorrect"}

    s = service(speculative=True, model=Slow())
    s.grader = ObservedGrader(ObservedGeminiGrader(invoke_grade=incorrect))
    row = await run_case({"id": "HN001", "question": "能否回答？"}, "B", 1, s, "snapshot-1")

    assert any(e["kind"] == "generation" and e["status"] == "cancelled" for e in row["events"])
    assert row["generation_evidence_ids"] == []
    assert row["web_fallback_executed"] is False
    assert row["insufficiency"]["origin"] == "program_template"


async def test_failed_compound_retrieval_cancels_and_records_unfinished_sibling():
    class OneFails:
        async def ainvoke(self, query):
            if query == "評估":
                await asyncio.sleep(0)
                raise ConnectionError("retrieval unavailable")
            await asyncio.sleep(10)
            return []

    row = await run_case({"id": "HN066", "question": "評估什麼，治療相同嗎？"},
                         "D", 1, service(decompose=True, retriever=OneFails()), "snapshot-1")

    assert row["run_status"] == "runtime_error"
    assert any(e["kind"] == "retrieval" and e["query"] == "治療" and e["status"] == "cancelled"
               for e in row["events"])
    assert all(e["status"] != "started" for e in row["events"])


async def test_jev_timeout_is_an_incident_even_when_gemini_fallback_answers():
    class SlowClient:
        async def post(self, *args, **kwargs):
            await asyncio.sleep(10)

    s = service()
    s.grader = ObservedGrader(ObservedJevGrader(api_key="test", client=SlowClient(),
        fallback=ObservedGeminiGrader(invoke_grade=correct)))
    row = await run_case({"id": "HN001", "question": "能否回答？"}, "B", 1, s, "snapshot-1")

    assert row["run_status"] == "completed_with_incidents"
    assert any(e["kind"] == "grader_provider" and e["provider"] == "Jev" and e["status"] == "timeout"
               for e in row["events"])
    assert any(e["error_type"] == "TimeoutError" for e in row["runtime_errors"])
    assert row["care_output"].startswith("依資料回答")


def test_formal_snapshot_cannot_fall_back_to_live_collection_or_table():
    with pytest.raises(ValueError):
        validate_snapshot({"snapshot_id": "new", "status": "verified_immutable"}, "live", "live")
    complete = {"snapshot_id": "new", "status": "verified_immutable", "mongo_collection": "frozen",
                "pg_table": "frozen.chunks", "mongo_search_index": "care_text_index",
                "mongo_sha256": "a" * 64, "pg_sha256": "b" * 64, "mongo_count": 1, "pg_count": 1}
    assert validate_snapshot(complete, "live", "live") == complete
    with pytest.raises(ValueError):
        validate_snapshot({**complete, "mongo_collection": "live"}, "live", "live")


@pytest.mark.parametrize("changed_text,index_ready,accepted", [
    (False, True, True), (True, True, False), (False, False, False),
])
async def test_physical_snapshot_checks_content_and_search_index(changed_text, index_ready, accepted):
    # Expected hashes come from literal serialized fixtures, independently of verify_snapshot.
    snapshot = {"mongo_sha256": hashlib.sha256('{"_id": "001", "chunk_content": "正文"}\n'.encode()).hexdigest(),
                "pg_sha256": hashlib.sha256('["001", "[1,2]"]\n'.encode()).hexdigest(),
                "mongo_count": 1, "pg_count": 1, "mongo_search_index": "frozen_index"}

    class Rows:
        def __init__(self, rows):
            self.rows = rows

        def sort(self, *args):
            return self

        def __aiter__(self):
            async def iterate():
                for row in self.rows:
                    yield row
            return iterate()

        async def to_list(self, *args):
            return self.rows

    class Collection:
        def find(self, query):
            return Rows([{"_id": "001", "chunk_content": "被改動" if changed_text else "正文"}])

        def list_search_indexes(self, name):
            return Rows([{"queryable": index_ready}])

    class Scope:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def transaction(self, **kwargs):
            return self

        def cursor(self, query):
            return Rows([{"id": "001", "embedding": "[1,2]"}])

    class Vector:
        vector_column = "embedding_half"
        table_name = "frozen.chunks"

        def _ensure_collection(self):
            return Collection()

        async def _ensure_pool(self):
            return SimpleNamespace(acquire=Scope)

    if accepted:
        actual = await verify_snapshot(Vector(), snapshot)
        assert actual["mongo_count"] == actual["pg_count"] == 1
    else:
        with pytest.raises(ValueError):
            await verify_snapshot(Vector(), snapshot)
