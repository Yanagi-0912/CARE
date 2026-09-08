"""共用的 Motor client 工廠：同一個 URI 只建一條連線，並統一套用逾時政策。

**為什麼要共用**：先前 `MongoAtlasVectorRetriever`、`MongoAtlasTextRetriever`
與 `MongoDBManager` 各自 `AsyncIOMotorClient(uri)`，同一個叢集因此被建了三條
獨立連線，每條各自付一次 TLS＋拓撲探索＋認證。健康網路下實測約 0.7-0.9 秒
（穩態查詢 50-250ms），所以共用省下的是「每次啟動一次、每條連線一次」的
秒級成本，不大但確定。

**為什麼要設逾時**：PyMongo 的 `socketTimeoutMS` 預設是 `None`——也就是
連線建立之後，對方不回應就永遠掛著，沒有任何上限。這是與觀測無關的潛在
缺陷：只要對端在連線後停止回應，請求就會無限期掛住。

值刻意寬鬆（20/20/30 秒）。健康網路下建立連線 0.7-0.9 秒、穩態查詢
50-250ms，所以這些數字遠離正常分佈——它防的是「真正的無限等待」，不是
「比平常慢」。開發機的網路品質變異很大（見下方更正紀錄），設得太緊會在
網路不佳時把慢變成錯。

**更正紀錄（2026-09-02）**：本模組最初的說明寫著「建立連線約 12 秒」，
並據此推論是本機到 Atlas 的網路路徑問題。那個數字是假的——量測期間開發機
有一條 OpenVPN 留下的殘留靜態路由（`140.121.196.16/32 → 10.24.21.1`，指向
已不存在的網段），VPN 因此卡在重連迴圈並改動了系統 DNS。刪掉那條路由後
同一組量測：純 TCP 連線 18/18 成功、中位 59ms，建立連線 688-876ms。
留這段話是因為那個錯誤數字一度被寫進四個檔案的註解與一個測試的門檻。
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
