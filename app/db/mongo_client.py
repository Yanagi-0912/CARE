"""共用的 Motor client 工廠：同一個 URI 只建一條連線，並統一套用逾時政策。

**為什麼要共用**：先前 `MongoAtlasVectorRetriever`、`MongoAtlasTextRetriever`
與 `MongoDBManager` 各自 `AsyncIOMotorClient(uri)`，同一個叢集因此被建了三條
獨立連線，每條各自付一次 TLS＋拓撲探索＋認證。本機實測那個成本約 12 秒
（DNS 只佔 58ms，穩態查詢是 50–250ms），所以重複建立不是省下毫秒的問題。

**為什麼要設逾時**：PyMongo 的 `socketTimeoutMS` 預設是 `None`——也就是
連線建立之後，對方不回應就永遠掛著，沒有任何上限。實測撞過一次
`stage=rag_retrieve ms=94652` 然後回 0 筆，使用者等 107 秒換到一句「查無資料」。

值的取法刻意寬鬆：穩態查詢是 50–250ms，冷啟動建立連線約 12 秒，所以這些
數字遠離正常分佈，防的是「真正的無限等待」而不是「比平常慢」。**設得比冷
啟動成本低會讓每次重啟後的第一個請求必定失敗**——那是把偶發的慢換成穩定的
錯，比原本更糟。
"""

from __future__ import annotations

import logging

from motor.motor_asyncio import AsyncIOMotorClient

from app.core.config import settings

logger = logging.getLogger(__name__)

_CLIENTS: dict[str, AsyncIOMotorClient] = {}


def get_shared_client(mongo_uri: str) -> AsyncIOMotorClient:
    """回傳這個 URI 對應的共用 client，必要時建立。

    以 URI 為鍵而不是全域單例：測試與腳本可能指向不同叢集，那時仍該是
    不同的連線。
    """
    uri = (mongo_uri or "").strip()
    if not uri:
        raise ValueError("mongo_uri 不可為空")

    client = _CLIENTS.get(uri)
    if client is not None:
        return client

    logger.info(
        "Creating shared Motor client (serverSelection=%sms connect=%sms socket=%sms)",
        settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS,
        settings.MONGODB_CONNECT_TIMEOUT_MS,
        settings.MONGODB_SOCKET_TIMEOUT_MS,
    )
    client = AsyncIOMotorClient(
        uri,
        serverSelectionTimeoutMS=settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS,
        connectTimeoutMS=settings.MONGODB_CONNECT_TIMEOUT_MS,
        socketTimeoutMS=settings.MONGODB_SOCKET_TIMEOUT_MS,
    )
    _CLIENTS[uri] = client
    return client


def reset_shared_clients() -> None:
    """清掉快取（供測試使用）。不關閉 client，呼叫端自行負責。"""
    _CLIENTS.clear()
