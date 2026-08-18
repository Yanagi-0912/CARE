"""已查核主張比對：拿正規化後的主張，去知識庫比對「這則主張是不是已經被查核過」。

結構照抄 `retriever.py` 的 `MongoAtlasVectorRetriever`（同樣 `$vectorSearch`、
同樣 `AsyncIOMotorClient`、同樣 lazy `_ensure_collection()`）。`$vectorSearch`
的 `path` 沿用與內容檢索**同一個** `embedding` 向量欄位（design.md 決策 2：
claim 本身是純文字、資料庫裡沒有另外的 claim 向量，改為直接查既有的
embedding 索引，結果裡挑 `verdict` 非空者）。差別只在於多一道 `verdict`
過濾，以及輸出型別是單一 `ClaimMatch | None` 而非 `list[Document]`
——這裡要的是「有沒有比對到」，不是排序後的候選清單。

`vector_field`（預設 `"embedding"`，供 `$vectorSearch` 用）與 `claim_field`
（預設 `"claim"`，只用來把命中文件的主張原文讀出來供 `ClaimMatch.claim`
參考）是兩個不同欄位，不可合而為一：2026-08-18 對 production Atlas 實測
證實，把兩者當同一個參數、預設指向 `"claim"` 送進 `$vectorSearch` 的
`path`，會讓 MongoDB 回 `OperationFailure`（claim 不是向量索引），而
`match()` 的 fail-open 設計會把這個例外整個吞掉、每次都靜默降級成
「未命中」——這正是一開始只有 `claim_field` 這一個參數時留下的陷阱。

台灣事實查核中心（TFC）文章的所有 chunk 共用同一篇的 `claim`／`verdict`
（它們是文章層級屬性），向量檢索因此會把同一篇的多個 chunk 都撈回來；
若不先以 `url` 去重，top-k 會被單篇洗版，讓真正該進來比較的其他文章被擠掉。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger(__name__)

_NUM_CANDIDATES_MULTIPLIER = 30

# 真正生效的門檻由 CLAIM_MATCH_MIN_SCORE 注入（見 design.md 決策 3：
# 誤配是本設計唯一嚴重的失效模式，門檻寧缺勿濫）。這裡的預設值只是建構子
# 沒被覆寫時的備援，tasks 1.2 已完成校準，數字與理由見 config.py。
DEFAULT_CLAIM_MATCH_MIN_SCORE = 0.86


@dataclass(frozen=True)
class ClaimMatch:
    claim: str
    verdict: str
    verdict_slug: str
    url: str
    title: str
    content: str
    score: float


class ClaimMatcher(Protocol):
    async def match(self, claim: str) -> ClaimMatch | None: ...


class MongoAtlasClaimMatcher:
    """正規化主張 → embedding → MongoDB `$vectorSearch`（既有內容向量欄位）→ 是否已被查核過。"""

    def __init__(
        self,
        *,
        embeddings: Any,
        mongo_uri: str,
        db_name: str,
        collection_name: str,
        index_name: str,
        vector_field: str = "embedding",
        claim_field: str = "claim",
        content_field: str = "chunk_content",
        k: int = 10,
        min_score: float = DEFAULT_CLAIM_MATCH_MIN_SCORE,
    ) -> None:
        self.embeddings = embeddings
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.collection_name = collection_name
        self.index_name = index_name
        self.vector_field = vector_field
        self.claim_field = claim_field
        self.content_field = content_field
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
                ("MONGODB_CLAIM_VECTOR_INDEX", self.index_name),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Missing {', '.join(missing)}")

        client = AsyncIOMotorClient(self.mongo_uri)
        self._collection = client[self.db_name][self.collection_name]
        return self._collection

    async def match(self, claim: str) -> ClaimMatch | None:
        # 對齊既有 RAG_HYBRID_ENABLED 在文字索引未建時的 fail-open 處置：
        # 這裡的任何失敗（缺設定、索引不存在、連線失敗、回傳格式異常）都不該
        # 中斷查核流程，一律降級為「未命中」，交由上層判定「證據不足」。
        try:
            raw_docs = await self._search(claim)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "claim match failed, degrading to no match: %s", exc, exc_info=True
            )
            return None

        best = self._best_verdicted_match(raw_docs)
        if best is None:
            return None

        score = best["score"]
        if score < self.min_score:
            return None

        return ClaimMatch(
            claim=str(best.get(self.claim_field) or ""),
            verdict=str(best.get("verdict") or ""),
            verdict_slug=str(best.get("verdict_slug") or ""),
            url=str(best.get("url") or ""),
            title=str(best.get("original_title") or ""),
            content=str(best.get(self.content_field) or ""),
            score=float(score),
        )

    async def _search(self, claim: str) -> list[dict[str, Any]]:
        query_embedding = await self.embeddings.aembed_query(claim)
        if not query_embedding:
            return []

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
            # 只有 TFC 的文件帶 verdict；寫明確的過濾條件，不要依賴「其他來源
            # 剛好沒有 claim 欄位」這個巧合。
            {"$match": {"verdict": {"$ne": None}}},
            {
                "$project": {
                    "_id": 0,
                    self.claim_field: 1,
                    "verdict": 1,
                    "verdict_slug": 1,
                    "url": 1,
                    "original_title": 1,
                    self.content_field: 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
        ]
        return await self._ensure_collection().aggregate(pipeline).to_list(length=None)

    def _best_verdicted_match(
        self, raw_docs: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """先濾掉沒有 verdict 的文件，同一 url 只留最高分那筆，再取全域最高分。

        `$match` 已在 pipeline 裡濾過 verdict，這裡再做一次是防禦性的：
        假 collection（測試）或未來換掉的儲存層不保證真的執行了那個 stage。
        """
        best_by_url: dict[str, dict[str, Any]] = {}
        for doc in raw_docs:
            if not doc.get("verdict"):
                continue
            score = doc.get("score")
            if not isinstance(score, (int, float)):
                continue
            url = str(doc.get("url") or "")
            current_best = best_by_url.get(url)
            if current_best is None or score > current_best["score"]:
                best_by_url[url] = doc

        if not best_by_url:
            return None
        return max(best_by_url.values(), key=lambda doc: doc["score"])
