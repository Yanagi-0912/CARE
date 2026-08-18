import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

if "motor.motor_asyncio" not in sys.modules:
    motor_module = types.ModuleType("motor")
    motor_asyncio_module = types.ModuleType("motor.motor_asyncio")
    motor_asyncio_module.AsyncIOMotorClient = object
    motor_asyncio_module.AsyncIOMotorCollection = object
    motor_module.motor_asyncio = motor_asyncio_module
    sys.modules["motor"] = motor_module
    sys.modules["motor.motor_asyncio"] = motor_asyncio_module

from langchain_core.documents import Document

from app.services.rag.retriever import (
    HybridRetriever,
    MongoAtlasTextRetriever,
    MongoAtlasVectorRetriever,
)


def _make_retriever(**overrides):
    emb = MagicMock()
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
    kwargs = {
        "embeddings": emb,
        "mongo_uri": "mongodb://localhost",
        "db_name": "db",
        "collection_name": "coll",
        "index_name": "idx",
        "vector_field": "embedding",
        "text_field": "chunk_text",
        "vector_dim": 3,
        "k": 4,
    }
    kwargs.update(overrides)
    return MongoAtlasVectorRetriever(**kwargs), emb


@pytest.mark.asyncio
async def test_retriever_rejects_empty_embedding():
    retriever, emb = _make_retriever()
    emb.aembed_query = AsyncMock(return_value=[])
    with pytest.raises(ValueError, match="cannot be empty"):
        await retriever.ainvoke("高血壓")


@pytest.mark.asyncio
async def test_retriever_rejects_wrong_dimension():
    retriever, emb = _make_retriever(vector_dim=3)
    emb.aembed_query = AsyncMock(return_value=[1.0, 2.0])
    with pytest.raises(ValueError, match="維度必須為 3"):
        await retriever.ainvoke("高血壓")


@pytest.mark.asyncio
async def test_retriever_returns_documents():
    retriever, emb = _make_retriever(vector_dim=2)
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2])

    fake_cursor = MagicMock()
    fake_cursor.to_list = AsyncMock(
        return_value=[
            {
                "_id": 123,
                "chunk_text": "A",
                "score": 0.9,
                "source_name": "來源A",
                "url": "https://a.example",
            },
            {"_id": "b", "chunk_text": "B", "score": 0.55},
            {"_id": "c", "chunk_text": "   ", "score": 0.9},
        ]
    )
    fake_collection = MagicMock()
    fake_collection.aggregate.return_value = fake_cursor
    retriever._collection = fake_collection

    docs = await retriever.ainvoke("高血壓")

    assert len(docs) == 2
    assert docs[0] == Document(
        page_content="A",
        metadata={
            "id": "123",
            "score": 0.9,
            "source_name": "來源A",
            "url": "https://a.example",
            "original_title": None,
            "verdict": None,
        },
    )
    assert docs[1].page_content == "B"
    emb.aembed_query.assert_awaited_once_with("高血壓")

    pipeline = fake_collection.aggregate.call_args.args[0]
    assert pipeline[0]["$vectorSearch"]["index"] == "idx"
    assert pipeline[0]["$vectorSearch"]["queryVector"] == [0.1, 0.2]
    assert pipeline[0]["$vectorSearch"]["limit"] == 4
    assert pipeline[0]["$vectorSearch"]["numCandidates"] == 120
    assert pipeline[1]["$project"]["chunk_text"] == 1


@pytest.mark.asyncio
async def test_retriever_filters_by_min_score():
    retriever, emb = _make_retriever(vector_dim=2, min_score=0.5)
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2])

    fake_cursor = MagicMock()
    fake_cursor.to_list = AsyncMock(
        return_value=[
            {"_id": "keep", "chunk_text": "高血壓飲食", "score": 0.5},
            {"_id": "drop-low", "chunk_text": "不相關", "score": 0.49},
            {"_id": "drop-none", "chunk_text": "沒分數"},
        ]
    )
    fake_collection = MagicMock()
    fake_collection.aggregate.return_value = fake_cursor
    retriever._collection = fake_collection

    docs = await retriever.ainvoke("高血壓")

    assert len(docs) == 1
    assert docs[0].page_content == "高血壓飲食"
    assert docs[0].metadata["score"] == 0.5


@pytest.mark.asyncio
async def test_retriever_keeps_low_score_docs_by_default():
    retriever, emb = _make_retriever(vector_dim=2)
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2])

    fake_cursor = MagicMock()
    fake_cursor.to_list = AsyncMock(
        return_value=[
            {"_id": "1", "chunk_text": "高分", "score": 0.9},
            {"_id": "2", "chunk_text": "低分", "score": 0.12},
        ]
    )
    fake_collection = MagicMock()
    fake_collection.aggregate.return_value = fake_cursor
    retriever._collection = fake_collection

    docs = await retriever.ainvoke("高血壓")

    # 過濾職責移交 reranker，第一階段不再砍低分候選
    assert [d.page_content for d in docs] == ["高分", "低分"]


@pytest.mark.asyncio
async def test_retriever_still_honours_explicit_min_score():
    retriever, emb = _make_retriever(vector_dim=2, min_score=0.5)
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2])

    fake_cursor = MagicMock()
    fake_cursor.to_list = AsyncMock(
        return_value=[
            {"_id": "1", "chunk_text": "高分", "score": 0.9},
            {"_id": "2", "chunk_text": "低分", "score": 0.12},
        ]
    )
    fake_collection = MagicMock()
    fake_collection.aggregate.return_value = fake_cursor
    retriever._collection = fake_collection

    docs = await retriever.ainvoke("高血壓")
    assert [d.page_content for d in docs] == ["高分"]


# ── MongoAtlasTextRetriever（BM25）─────────────────────────────────


def _make_text_retriever(**overrides):
    kwargs = {
        "mongo_uri": "mongodb://localhost",
        "db_name": "db",
        "collection_name": "coll",
        "index_name": "text_idx",
        "text_field": "chunk_text",
        "k": 4,
    }
    kwargs.update(overrides)
    return MongoAtlasTextRetriever(**kwargs)


def _fake_collection(rows):
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=rows)
    collection = MagicMock()
    collection.aggregate.return_value = cursor
    return collection


@pytest.mark.asyncio
async def test_text_retriever_builds_search_pipeline():
    retriever = _make_text_retriever()
    collection = _fake_collection(
        [{"_id": 1, "chunk_text": "乙醯胺酚每日上限", "score": 8.4}]
    )
    retriever._collection = collection

    docs = await retriever.ainvoke("乙醯胺酚一天最多幾顆")

    assert len(docs) == 1
    assert docs[0].page_content == "乙醯胺酚每日上限"
    assert docs[0].metadata == {
        "id": "1",
        "score": 8.4,
        "source_name": None,
        "url": None,
        "original_title": None,
        "verdict": None,
    }

    pipeline = collection.aggregate.call_args.args[0]
    assert pipeline[0]["$search"]["index"] == "text_idx"
    assert pipeline[0]["$search"]["text"] == {
        "query": "乙醯胺酚一天最多幾顆",
        "path": "chunk_text",
    }
    assert pipeline[1]["$limit"] == 4
    assert pipeline[2]["$project"]["score"] == {"$meta": "searchScore"}


@pytest.mark.asyncio
async def test_text_retriever_also_matches_title_when_configured():
    """chunk_content 不含標題，BM25 必須另外比對 original_title。"""
    retriever = _make_text_retriever(title_field="original_title", title_boost=1.5)
    collection = _fake_collection([{"_id": 1, "chunk_text": "內容", "score": 3.0}])
    retriever._collection = collection

    await retriever.ainvoke("退燒藥")

    stage = collection.aggregate.call_args.args[0][0]["$search"]
    assert "text" not in stage, "設了 title_field 就該改用 compound"
    assert stage["compound"]["minimumShouldMatch"] == 1
    assert stage["compound"]["should"] == [
        {"text": {"query": "退燒藥", "path": "chunk_text"}},
        {
            "text": {
                "query": "退燒藥",
                "path": "original_title",
                "score": {"boost": {"value": 1.5}},
            }
        },
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_text_retriever_falls_back_to_content_only_without_title_field(blank):
    """title_field 留空＝關閉標題比對，退回原本的單欄位 $search。"""
    retriever = _make_text_retriever(title_field=blank)
    collection = _fake_collection([{"_id": 1, "chunk_text": "內容", "score": 3.0}])
    retriever._collection = collection

    await retriever.ainvoke("退燒藥")

    stage = collection.aggregate.call_args.args[0][0]["$search"]
    assert "compound" not in stage
    assert stage["text"] == {"query": "退燒藥", "path": "chunk_text"}


@pytest.mark.asyncio
async def test_text_retriever_keeps_low_bm25_scores():
    """BM25 分數沒有上界也依語料庫而變，不能套用向量那個 0.5 門檻。"""
    retriever = _make_text_retriever()
    retriever._collection = _fake_collection(
        [
            {"_id": "a", "chunk_text": "低分但相關", "score": 0.12},
            {"_id": "b", "chunk_text": "沒有分數欄位"},
        ]
    )

    docs = await retriever.ainvoke("查詢")

    assert [d.metadata["id"] for d in docs] == ["a", "b"]
    assert docs[0].metadata["score"] == 0.12
    assert docs[1].metadata["score"] == 0.0


@pytest.mark.asyncio
async def test_text_retriever_skips_blank_text_and_empty_query():
    retriever = _make_text_retriever()
    retriever._collection = _fake_collection(
        [{"_id": "blank", "chunk_text": "   ", "score": 9.9}]
    )

    assert await retriever.ainvoke("查詢") == []
    assert await retriever.ainvoke("   ") == []


def test_text_retriever_requires_text_index_name():
    retriever = _make_text_retriever(index_name="")
    with pytest.raises(ValueError, match="MONGODB_TEXT_INDEX"):
        retriever._ensure_collection()


# ── HybridRetriever ───────────────────────────────────────────────


def _stub_retriever(docs=None, error: Exception | None = None):
    stub = MagicMock()
    if error is not None:
        stub.ainvoke = AsyncMock(side_effect=error)
    else:
        stub.ainvoke = AsyncMock(return_value=docs or [])
    return stub


def _d(doc_id: str, score: float = 1.0) -> Document:
    return Document(
        page_content=f"content-{doc_id}", metadata={"id": doc_id, "score": score}
    )


@pytest.mark.asyncio
async def test_hybrid_fuses_both_sources():
    hybrid = HybridRetriever(
        vector_retriever=_stub_retriever([_d("only-vector"), _d("both")]),
        text_retriever=_stub_retriever([_d("both"), _d("only-text")]),
    )

    docs = await hybrid.ainvoke("乙醯胺酚劑量")

    ids = [d.metadata["id"] for d in docs]
    assert ids[0] == "both"  # 兩邊都命中的浮上來
    assert set(ids) == {"both", "only-vector", "only-text"}
    assert docs[0].metadata["retrievers"] == ["vector", "text"]


@pytest.mark.asyncio
async def test_hybrid_degrades_to_vector_when_text_search_fails():
    """
    Atlas Search index 還沒建好時 $search 會報錯。
    此時必須退化為純向量，不能讓整條 RAG 掛掉。
    """
    hybrid = HybridRetriever(
        vector_retriever=_stub_retriever([_d("v1"), _d("v2")]),
        text_retriever=_stub_retriever(error=RuntimeError("index not found")),
    )

    docs = await hybrid.ainvoke("查詢")

    assert [d.metadata["id"] for d in docs] == ["v1", "v2"]


@pytest.mark.asyncio
async def test_hybrid_degrades_to_text_when_vector_fails():
    hybrid = HybridRetriever(
        vector_retriever=_stub_retriever(error=RuntimeError("embedding down")),
        text_retriever=_stub_retriever([_d("t1")]),
    )

    docs = await hybrid.ainvoke("查詢")

    assert [d.metadata["id"] for d in docs] == ["t1"]


@pytest.mark.asyncio
async def test_hybrid_returns_empty_when_both_fail():
    hybrid = HybridRetriever(
        vector_retriever=_stub_retriever(error=RuntimeError("a")),
        text_retriever=_stub_retriever(error=RuntimeError("b")),
    )
    assert await hybrid.ainvoke("查詢") == []


@pytest.mark.asyncio
async def test_hybrid_applies_limit_and_passes_query_to_both():
    vector = _stub_retriever([_d("a"), _d("b"), _d("c")])
    text = _stub_retriever([_d("c")])
    hybrid = HybridRetriever(
        vector_retriever=vector, text_retriever=text, limit=2, rrf_k=60
    )

    docs = await hybrid.ainvoke("高血壓")

    assert len(docs) == 2
    vector.ainvoke.assert_awaited_once_with("高血壓")
    text.ainvoke.assert_awaited_once_with("高血壓")


def test_ensure_collection_creates_motor_collection_once():
    retriever, _emb = _make_retriever()

    fake_collection = MagicMock()
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_collection
    fake_client = MagicMock()
    fake_client.__getitem__.return_value = fake_db

    from unittest.mock import patch

    with patch(
        "app.services.rag.retriever.AsyncIOMotorClient",
        return_value=fake_client,
    ) as mock_motor:
        first = retriever._ensure_collection()
        second = retriever._ensure_collection()

    assert first is fake_collection
    assert second is fake_collection
    mock_motor.assert_called_once_with("mongodb://localhost")


# ── original_title 投影 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_vector_retriever_projects_and_exposes_original_title():
    retriever, emb = _make_retriever(vector_dim=2)
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2])

    fake_cursor = MagicMock()
    fake_cursor.to_list = AsyncMock(
        return_value=[
            {
                "_id": "abc",
                "chunk_text": "幽門螺旋桿菌與胃癌風險",
                "source_name": "食藥署闢謠專區",
                "url": None,
                "original_title": "捍「胃」健康 過年聚餐用公筷",
                "score": 0.8,
            }
        ]
    )
    fake_collection = MagicMock()
    fake_collection.aggregate.return_value = fake_cursor
    retriever._collection = fake_collection

    docs = await retriever.ainvoke("幽門螺旋桿菌")

    pipeline = fake_collection.aggregate.call_args.args[0]
    project_stage = next(s for s in pipeline if "$project" in s)
    assert project_stage["$project"]["original_title"] == 1
    assert docs[0].metadata["original_title"] == "捍「胃」健康 過年聚餐用公筷"


# ── verdict 投影（claim-verdict-card 最終 review C1 finding）：查核判定卡
# 「相關衛教資訊」要能排除 TFC 查核報告，前提是 retriever 本身要先把
# verdict 投影進 metadata，否則下游無資料可過濾 ──────────────────────────


@pytest.mark.asyncio
async def test_vector_retriever_projects_and_exposes_verdict():
    retriever, emb = _make_retriever(vector_dim=2)
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2])

    fake_cursor = MagicMock()
    fake_cursor.to_list = AsyncMock(
        return_value=[
            {
                "_id": "tfc-1",
                "chunk_text": "查核中心訪問多位醫師⋯",
                "url": "https://tfc.example/1",
                "original_title": "【錯誤】網傳吃洋蔥可以降血壓？",
                "verdict": "錯誤",
                "score": 0.9,
            }
        ]
    )
    fake_collection = MagicMock()
    fake_collection.aggregate.return_value = fake_cursor
    retriever._collection = fake_collection

    docs = await retriever.ainvoke("洋蔥降血壓")

    pipeline = fake_collection.aggregate.call_args.args[0]
    project_stage = next(s for s in pipeline if "$project" in s)
    assert project_stage["$project"]["verdict"] == 1
    assert docs[0].metadata["verdict"] == "錯誤"


@pytest.mark.asyncio
async def test_vector_retriever_verdict_metadata_is_none_for_non_tfc_docs():
    """闢謠網站／食藥署等一般衛教來源沒有 verdict 欄位，metadata 裡應為
    None 而非缺 key——下游用 `.get("verdict")` 判斷才不會出錯。"""
    retriever, emb = _make_retriever(vector_dim=2)
    emb.aembed_query = AsyncMock(return_value=[0.1, 0.2])

    fake_cursor = MagicMock()
    fake_cursor.to_list = AsyncMock(
        return_value=[
            {"_id": "edu-1", "chunk_text": "高血壓的飲食建議⋯", "score": 0.8}
        ]
    )
    fake_collection = MagicMock()
    fake_collection.aggregate.return_value = fake_cursor
    retriever._collection = fake_collection

    docs = await retriever.ainvoke("高血壓飲食")

    assert docs[0].metadata["verdict"] is None


@pytest.mark.asyncio
async def test_text_retriever_projects_and_exposes_original_title():
    retriever = _make_text_retriever()
    collection = _fake_collection(
        [
            {
                "_id": "abc",
                "chunk_text": "幽門螺旋桿菌與胃癌風險",
                "source_name": "食藥署闢謠專區",
                "url": None,
                "original_title": "捍「胃」健康 過年聚餐用公筷",
                "score": 8.4,
            }
        ]
    )
    retriever._collection = collection

    docs = await retriever.ainvoke("幽門螺旋桿菌")

    pipeline = collection.aggregate.call_args.args[0]
    project_stage = next(s for s in pipeline if "$project" in s)
    assert project_stage["$project"]["original_title"] == 1
    assert docs[0].metadata["original_title"] == "捍「胃」健康 過年聚餐用公筷"


@pytest.mark.asyncio
async def test_text_retriever_projects_and_exposes_verdict():
    """HybridRetriever 同時跑向量與 BM25 兩邊，若只有向量那側投影 verdict，
    融合結果裡經由 BM25 命中的 TFC 文件仍會漏掉這個 metadata、逃過下游的
    verdict 過濾。BM25 這側也要能獨立投影出 verdict。"""
    retriever = _make_text_retriever()
    collection = _fake_collection(
        [
            {
                "_id": "tfc-1",
                "chunk_text": "查核中心訪問多位醫師⋯",
                "url": "https://tfc.example/1",
                "original_title": "【錯誤】網傳吃洋蔥可以降血壓？",
                "verdict": "錯誤",
                "score": 5.0,
            }
        ]
    )
    retriever._collection = collection

    docs = await retriever.ainvoke("洋蔥降血壓")

    pipeline = collection.aggregate.call_args.args[0]
    project_stage = next(s for s in pipeline if "$project" in s)
    assert project_stage["$project"]["verdict"] == 1
    assert docs[0].metadata["verdict"] == "錯誤"
