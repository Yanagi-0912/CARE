"""PostgreSQL + pgvector 的向量檢索（async Runnable 風格：ainvoke）。

與 `retriever.MongoAtlasVectorRetriever` 介面完全相同（`ainvoke(query) ->
list[Document]`），所以可以直接替換注入 `HybridRetriever`，融合邏輯與下游流程
都不必改。

為什麼向量搬出 Atlas：
    Atlas 免費層上限 512 MB，而 3072 維向量用 BSON double array 存，一筆 chunk
    就要 42 KB——內文只佔 1.4 KB，96% 的空間在向量。2026-09-19 因此撞到配額、
    寫入被鎖，後端啟動時的 ensure_indexes() 被拒絕，新版 pod 起不來。把向量移到
    pgvector 之後 Atlas 降到約 51 MB。

為什麼內文留在 Mongo 而不是一起搬：
    BM25 那條腿（`MongoAtlasTextRetriever`）靠 Atlas Search 的 lucene.cjk 分詞，
    那是已經校準且線上跑順的。PG 這邊沒有等價的中文分詞（這台只有 pg_trgm，
    沒有 zhparser／pg_jieba），換過去等於換掉排序演算法，融合權重與檢索品質都
    要重測。容量問題單靠搬向量就解決了，內文只有 16 MB，所以這裡刻意只搬向量。
    代價是檢索要跨兩個系統：PG 拿 id、Mongo 撈內文。

為什麼用 halfvec 而不是 vector：
    pgvector 的 `vector` 型別索引上限 2000 維，3072 維建不了 HNSW；`halfvec`
    （16-bit）上限 4000 維可以。2026-09-19 實測：無索引的暴力掃描 213 ms，
    建了 HNSW 之後 1.6 ms，而 top-10 結果與 Atlas 完全一致。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.documents import Document

from app.core.request_logging import stage_timer
from app.db.mongo_client import get_shared_client

logger = logging.getLogger(__name__)

# 與 retriever._NUM_CANDIDATES_MULTIPLIER 同義：HNSW 要先撈一批候選再取前 k。
# pgvector 對應的旋鈕是 hnsw.ef_search，每個連線各自設定。
_EF_SEARCH_MULTIPLIER = 30

# ef_search 的下限。pgvector 預設 40，低於這個值召回率會掉得很快。
_MIN_EF_SEARCH = 40


def _distance_to_atlas_score(distance: float) -> float:
    """把 pgvector 的 cosine distance 換算成 Atlas `vectorSearchScore` 的標度。

    兩邊對 cosine 的定義不同，不換算的話 `min_score` 門檻與凸組合融合的權重
    都會失準：

        Atlas vectorSearchScore = (1 + cos_sim) / 2      範圍 [0, 1]
        pgvector `<=>`          = 1 - cos_sim            範圍 [0, 2]

    代入得 score = 1 - distance / 2。

    注意：這是從兩邊的定義推導的，**沒有實測對照過** ——換算公式寫出來時
    Atlas 的 embedding 已經清掉，`$vectorSearch` 沒有資料可比。切換上線前
    應該用同一批查詢比對兩邊的分數分布確認。所幸 `DEFAULT_MIN_SCORE` 目前是
    0.0（不過濾），且融合預設走 RRF（只看排名不看分數），所以就算換算有偏差，
    現行路徑的行為也不受影響。
    """
    return 1.0 - (distance / 2.0)


class PgVectorRetriever:
    """問題字串 → embedding → pgvector 取 top-k id → Mongo 撈內文 → `Document` 列表。"""

    def __init__(
        self,
        *,
        embeddings: Any,
        dsn: str,
        mongo_uri: str,
        db_name: str,
        collection_name: str,
        table_name: str = "health_articles_chunks",
        vector_column: str = "embedding_half",
        text_field: str = "chunk_content",
        vector_dim: int | None = None,
        k: int = 10,
        min_score: float = 0.0,
    ) -> None:
        self.embeddings = embeddings
        self.dsn = dsn
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.collection_name = collection_name
        self.table_name = table_name
        self.vector_column = vector_column
        self.text_field = text_field
        self.vector_dim = vector_dim
        self.k = k
        self.min_score = min_score
        self._pool: Any = None
        self._collection: Any = None

    async def _ensure_pool(self) -> Any:
        """連線池是懶建的：第一次查詢才連，與 Mongo client 的作法一致。"""
        if self._pool is not None:
            return self._pool

        if not self.dsn:
            raise ValueError("Missing PGVECTOR_DSN")

        import asyncpg  # 延後 import：沒設定 PG 時不該因為缺套件就起不來

        # min_size=1 讓連線在 warmup 後就保持著，第一個使用者不必付建線成本。
        # max_size 刻意小：這台 PG 與 MEDDEMO 共用，而且向量查詢是 1.6 ms 等級，
        # 不需要大池子。
        self._pool = await asyncpg.create_pool(
            self.dsn, min_size=1, max_size=4, command_timeout=10
        )
        return self._pool

    def _ensure_collection(self) -> Any:
        """內文仍在 Mongo，用與 MongoAtlasVectorRetriever 同一個共用 client。"""
        if self._collection is not None:
            return self._collection

        missing = [
            name
            for name, value in (
                ("MONGODB_URI", self.mongo_uri),
                ("MONGODB_DB", self.db_name),
                ("MONGODB_COLLECTION", self.collection_name),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing {', '.join(missing)}")

        client = get_shared_client(self.mongo_uri)
        self._collection = client[self.db_name][self.collection_name]
        return self._collection

    async def warmup(self) -> None:
        """兩邊的連線都先建起來，讓部署後第一個使用者不必獨自付這個成本。"""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        await self._ensure_collection().database.client.admin.command("ping")

    async def ainvoke(self, query: str) -> list[Document]:
        # 三段分開計時：embedding（Gemini）、向量搜尋（PG）、撈內文（Mongo）。
        # 只記總數的話，分不出慢在哪一段——這正是搬家前 rag_retrieve 有 94 秒
        # 肥尾卻查不出原因的教訓。
        with stage_timer(logger, "embed_query", retriever="kb"):
            query_embedding = await self.embeddings.aembed_query(query)
        if not query_embedding:
            raise ValueError("query_embedding cannot be empty")
        if self.vector_dim is not None and len(query_embedding) != self.vector_dim:
            raise ValueError(
                f"queryVector 維度必須為 {self.vector_dim}（與向量索引一致），"
                f"目前為 {len(query_embedding)}。"
                " 請用與建索引時相同的 embedding 模型產生完整向量。"
            )

        # asyncpg 不認得 halfvec，所以用 pgvector 的文字輸入格式傳再由 PG 轉型。
        # 少一個依賴（不必註冊型別），代價是每次查詢多一次字串組裝——3072 個
        # 浮點數，相對於整條 RAG 管線可以忽略。
        vector_literal = "[" + ",".join(repr(float(v)) for v in query_embedding) + "]"
        ef_search = max(_MIN_EF_SEARCH, self.k * _EF_SEARCH_MULTIPLIER)

        sql = (
            f"SELECT id, {self.vector_column} <=> $1::halfvec AS distance "
            f"FROM {self.table_name} "
            f"WHERE {self.vector_column} IS NOT NULL "
            f"ORDER BY {self.vector_column} <=> $1::halfvec "
            f"LIMIT $2"
        )

        pool = await self._ensure_pool()
        with stage_timer(logger, "vector_search", retriever="kb"):
            async with pool.acquire() as conn:
                # ef_search 是連線層級的設定，每次都設：連線池會重用連線，
                # 而 SET LOCAL 只在交易內有效，這裡沒有開交易。
                await conn.execute(f"SET hnsw.ef_search = {ef_search}")
                rows = await conn.fetch(sql, vector_literal, self.k)

        scored: list[tuple[str, float]] = []
        for row in rows:
            score = _distance_to_atlas_score(float(row["distance"]))
            if score < self.min_score:
                continue
            scored.append((str(row["id"]), score))

        if not scored:
            return []

        return await self._fetch_documents(scored)

    async def _fetch_documents(self, scored: list[tuple[str, float]]) -> list[Document]:
        """依 PG 給的 id 到 Mongo 撈內文，並保持 PG 的相似度排序。

        Mongo 的 `$in` 不保證回傳順序，而下游融合（RRF）吃的是排名，順序錯了
        等於分數錯了，所以這裡用 id → score 的對照表重新排。
        """
        from bson import ObjectId

        score_by_id = dict(scored)
        object_ids = []
        for raw_id in score_by_id:
            try:
                object_ids.append(ObjectId(raw_id))
            except Exception:
                # PG 的 id 是從 Mongo 的 _id 搬過來的，理論上一定合法。
                # 真的出現不合法值時跳過這一筆而不是整題失敗。
                logger.warning("pgvector_bad_object_id id=%s", raw_id)

        if not object_ids:
            return []

        projection = {
            self.text_field: 1,
            "_id": 1,
            "source_name": 1,
            "url": 1,
            "original_title": 1,
            # 與 MongoAtlasVectorRetriever 同步投影 verdict，理由見該處註解：
            # 查核判定卡的「相關衛教資訊」要靠這個欄位排除 TFC 報告。
            "verdict": 1,
        }
        with stage_timer(logger, "fetch_content", retriever="kb"):
            raw_docs = await (
                self._ensure_collection()
                .find({"_id": {"$in": object_ids}}, projection)
                .to_list(length=None)
            )

        documents: list[Document] = []
        for doc in raw_docs:
            text = str(doc.get(self.text_field) or "").strip()
            if not text:
                continue
            doc_id = str(doc.get("_id"))
            score = score_by_id.get(doc_id)
            if score is None:
                continue
            documents.append(
                Document(
                    page_content=text,
                    metadata={
                        "id": doc_id,
                        "score": score,
                        "source_name": doc.get("source_name"),
                        "url": doc.get("url"),
                        "original_title": doc.get("original_title"),
                        "verdict": doc.get("verdict"),
                    },
                )
            )

        # Mongo 少回的（例如內文被刪但 PG 的向量還在）會讓筆數對不上，
        # 記下來——那是兩邊不同步的訊號，正是雙寫架構最需要盯的失敗形態。
        if len(documents) != len(score_by_id):
            logger.warning(
                "pgvector_mongo_mismatch pg=%d mongo=%d",
                len(score_by_id),
                len(documents),
            )

        documents.sort(key=lambda d: d.metadata["score"], reverse=True)
        return documents
