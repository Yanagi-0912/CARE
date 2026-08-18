"""MongoDB Atlas 檢索（async Runnable 風格：ainvoke）。

三個實作共用同一個 `ainvoke(query) -> list[Document]` 介面，因此可以互換
注入 `RagAnswerService`，不需改動下游任何流程：

- `MongoAtlasVectorRetriever`：`$vectorSearch`，比對語意。
- `MongoAtlasTextRetriever`：`$search`，比對字面（BM25）。醫療查詢裡的
  藥名、劑量、疾病名這類罕見精確詞是稠密向量的弱項、稀疏檢索的強項。
- `HybridRetriever`：並行跑上面兩者，再以 RRF 融合。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langchain_core.documents import Document
from motor.motor_asyncio import AsyncIOMotorClient

from app.services.rag.rank_fusion import DEFAULT_RRF_K, reciprocal_rank_fusion

logger = logging.getLogger(__name__)

_NUM_CANDIDATES_MULTIPLIER = 30

# 第一階段負責衝 recall，過濾交給 reranker（見 openspec/changes/rag-retrieval-tuning）。
# 保留參數以便需要時由 env 調回。
# 注意：使用者上傳文件的檢索路徑沒有 reranker，因此不共用這個值，
# 見 user_document_retriever.DEFAULT_USER_DOC_MIN_SCORE。
DEFAULT_MIN_SCORE = 0.0

# BM25 比對標題時的權重。小於 1 是刻意的降權，理由見 config.RAG_TEXT_TITLE_BOOST。
DEFAULT_TITLE_BOOST = 0.3

VECTOR_SOURCE_NAME = "vector"
TEXT_SOURCE_NAME = "text"


class MongoAtlasVectorRetriever:
    """問題字串 → embedding → MongoDB `$vectorSearch` → `Document` 列表。"""

    def __init__(
        self,
        *,
        embeddings: Any,
        mongo_uri: str,
        db_name: str,
        collection_name: str,
        index_name: str,
        vector_field: str = "embedding",
        text_field: str = "text",
        vector_dim: int | None = None,
        k: int = 10,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> None:
        self.embeddings = embeddings
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.collection_name = collection_name
        self.index_name = index_name
        self.vector_field = vector_field
        self.text_field = text_field
        self.vector_dim = vector_dim
        self.k = k
        self.min_score = min_score
        self._collection: Any = None

    def _ensure_collection(self) -> Any:
        if self._collection is not None:
            return self._collection

        missing = [
            name
            for name, value in (
                ("MONGODB_URI", self.mongo_uri),
                ("MONGODB_DB", self.db_name),
                ("MONGODB_COLLECTION", self.collection_name),
                ("MONGODB_VECTOR_INDEX", self.index_name),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing {', '.join(missing)}")

        client = AsyncIOMotorClient(self.mongo_uri)
        self._collection = client[self.db_name][self.collection_name]
        return self._collection

    async def ainvoke(self, query: str) -> list[Document]:
        query_embedding = await self.embeddings.aembed_query(query)
        if not query_embedding:
            raise ValueError("query_embedding cannot be empty")
        if self.vector_dim is not None and len(query_embedding) != self.vector_dim:
            raise ValueError(
                f"queryVector 維度必須為 {self.vector_dim}（與向量索引一致），"
                f"目前為 {len(query_embedding)}。"
                " 請用與建索引時相同的 embedding 模型產生完整向量。"
            )

        pipeline = [
            {
                "$vectorSearch": {
                    "index": self.index_name,
                    "path": self.vector_field,
                    "queryVector": query_embedding,
                    "numCandidates": self.k * _NUM_CANDIDATES_MULTIPLIER,
                    "limit": self.k,
                }
            },
            {
                "$project": {
                    self.text_field: 1,
                    "_id": 1,
                    "source_name": 1,
                    "url": 1,
                    "original_title": 1,
                    # TFC 查核報告帶 verdict，其餘來源沒有這個欄位。一般 RAG
                    # 答問路徑（RagAnswerService）不看這個 metadata、行為不變；
                    # 加這個欄位是為了讓查核判定卡的「相關衛教資訊」區塊
                    # （claim_verification/service.py 的 _fetch_related_info）
                    # 能夠排除 TFC 文件——那些帶判定的報告不是衛教資訊
                    # （design.md 決策 4），過去這裡沒有投影 verdict，
                    # 下游就算想過濾也無資料可用（claim-verdict-card 最終
                    # review 的 C1 finding）。
                    "verdict": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
        ]

        raw_docs = await self._ensure_collection().aggregate(pipeline).to_list(length=None)

        documents: list[Document] = []
        for doc in raw_docs:
            text = str(doc.get(self.text_field) or "").strip()
            if not text:
                continue
            score = doc.get("score")
            if not isinstance(score, (int, float)) or score < self.min_score:
                continue
            documents.append(
                Document(
                    page_content=text,
                    metadata={
                        "id": str(doc.get("_id")),
                        "score": score,
                        "source_name": doc.get("source_name"),
                        "url": doc.get("url"),
                        "original_title": doc.get("original_title"),
                        "verdict": doc.get("verdict"),
                    },
                )
            )
        return documents


class MongoAtlasTextRetriever:
    """問題字串 → MongoDB Atlas `$search`（BM25）→ `Document` 列表。

    索引必須是 Atlas Search index（與 vector index 是兩個不同的東西），
    且中文語料的 analyzer 必須用 `lucene.cjk`；預設的英文 analyzer 會把
    整句中文當成一個 token，BM25 等於失效。

    設了 `title_field` 就同時比對標題。之所以需要：切塊後的 `chunk_content`
    不含標題，但 embedding（ETL 用 `主題：{title}\\n內容：{chunk}`）與 rerank
    （`rerank_document_text`）兩處都會把標題補回文本。只有 BM25 這條腿看不到
    標題，於是「藥名／疾病名只出現在標題」的文章，稀疏檢索完全命中不了——
    而罕見精確詞正是 BM25 該補向量之不足的地方。
    """

    def __init__(
        self,
        *,
        mongo_uri: str,
        db_name: str,
        collection_name: str,
        index_name: str,
        text_field: str = "text",
        title_field: str | None = None,
        title_boost: float = DEFAULT_TITLE_BOOST,
        k: int = 10,
    ) -> None:
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.collection_name = collection_name
        self.index_name = index_name
        self.text_field = text_field
        self.title_field = (title_field or "").strip() or None
        self.title_boost = title_boost
        self.k = k
        self._collection: Any = None

    def _search_stage(self, query: str) -> dict[str, Any]:
        """`$search` 的內容。有設 title_field 時改用 compound 同時比對標題。

        兩個 clause 都放在 `should`（`minimumShouldMatch: 1`），語意是「內文
        或標題任一命中即可」。放 `must` 會變成兩者都要命中，比原本更嚴，
        那不是這個改動的目的。
        """
        content_clause = {"text": {"query": query, "path": self.text_field}}
        if self.title_field is None:
            return {"index": self.index_name, **content_clause}

        title_clause = {
            "text": {
                "query": query,
                "path": self.title_field,
                "score": {"boost": {"value": self.title_boost}},
            }
        }
        return {
            "index": self.index_name,
            "compound": {
                "should": [content_clause, title_clause],
                "minimumShouldMatch": 1,
            },
        }

    def _ensure_collection(self) -> Any:
        if self._collection is not None:
            return self._collection

        missing = [
            name
            for name, value in (
                ("MONGODB_URI", self.mongo_uri),
                ("MONGODB_DB", self.db_name),
                ("MONGODB_COLLECTION", self.collection_name),
                ("MONGODB_TEXT_INDEX", self.index_name),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing {', '.join(missing)}")

        client = AsyncIOMotorClient(self.mongo_uri)
        self._collection = client[self.db_name][self.collection_name]
        return self._collection

    async def ainvoke(self, query: str) -> list[Document]:
        if not (query or "").strip():
            return []

        pipeline = [
            {"$search": self._search_stage(query)},
            {"$limit": self.k},
            {
                "$project": {
                    self.text_field: 1,
                    "_id": 1,
                    "source_name": 1,
                    "url": 1,
                    "original_title": 1,
                    # 與 MongoAtlasVectorRetriever 同步投影 verdict，理由見該處
                    # 註解：HybridRetriever 融合向量與 BM25 兩邊結果，若只有
                    # 一邊帶 verdict metadata，經過融合後仍可能漏掉沒被過濾到
                    # 的 TFC 文件。
                    "verdict": 1,
                    "score": {"$meta": "searchScore"},
                }
            },
        ]

        raw_docs = await self._ensure_collection().aggregate(pipeline).to_list(length=None)

        documents: list[Document] = []
        for doc in raw_docs:
            text = str(doc.get(self.text_field) or "").strip()
            if not text:
                continue
            # 刻意不做 min_score 過濾：BM25 分數沒有上界、也依語料庫統計而變，
            # 固定門檻沒有意義（向量的 0.5 門檻是針對 cosine 相似度才成立）。
            score = doc.get("score")
            documents.append(
                Document(
                    page_content=text,
                    metadata={
                        "id": str(doc.get("_id")),
                        "score": float(score) if isinstance(score, (int, float)) else 0.0,
                        "source_name": doc.get("source_name"),
                        "url": doc.get("url"),
                        "original_title": doc.get("original_title"),
                        "verdict": doc.get("verdict"),
                    },
                )
            )
        return documents


class HybridRetriever:
    """並行跑向量與文字檢索，再以 RRF 融合成單一排名。

    任一邊失敗只記錄並降級為另一邊的結果（fail-open）。這讓本類別在
    Atlas Search index 還沒建好時也能安全上線 —— 那時 `$search` 會報錯，
    行為自動退化為原本的純向量檢索。
    """

    def __init__(
        self,
        *,
        vector_retriever: Any,
        text_retriever: Any,
        rrf_k: int = DEFAULT_RRF_K,
        limit: int | None = None,
    ) -> None:
        self.vector_retriever = vector_retriever
        self.text_retriever = text_retriever
        self.rrf_k = rrf_k
        self.limit = limit

    async def ainvoke(self, query: str) -> list[Document]:
        vector_docs, text_docs = await asyncio.gather(
            self._safe_invoke(self.vector_retriever, VECTOR_SOURCE_NAME, query),
            self._safe_invoke(self.text_retriever, TEXT_SOURCE_NAME, query),
        )

        fused = reciprocal_rank_fusion(
            [
                (VECTOR_SOURCE_NAME, vector_docs),
                (TEXT_SOURCE_NAME, text_docs),
            ],
            k=self.rrf_k,
            limit=self.limit,
        )
        logger.info(
            "hybrid_retrieve vector=%d text=%d fused=%d",
            len(vector_docs),
            len(text_docs),
            len(fused),
        )
        return fused

    @staticmethod
    async def _safe_invoke(retriever: Any, name: str, query: str) -> list[Document]:
        try:
            return await retriever.ainvoke(query)
        except Exception:
            logger.exception("hybrid_retrieve_failed source=%s; degrading", name)
            return []
