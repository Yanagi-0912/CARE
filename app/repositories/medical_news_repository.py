"""每日醫療消息卡的資料庫操作。

慣例與 `medication_repository.py` 一致：方法皆為 `@staticmethod`，且尾參數是
`collection: Optional[Any] = None`。測試靠這個參數注入替身——本專案禁止以
monkey patch 改寫全域或別處導入的實例（openspec/config.yaml rules.tasks）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.db.mongodb import MongoDBManager
from app.models.medical_news import DrugNews, MedicalNewsDelivery

logger = logging.getLogger(__name__)


async def ensure_indexes(
    *,
    drug_news_collection: Optional[Any] = None,
    deliveries_collection: Optional[Any] = None,
    shares_collection: Optional[Any] = None,
) -> None:
    """建立三個 collection 的索引。

    刻意是模組層函式而非某個 class 的方法：三個 collection 的索引要一起建立，
    掛在其中任一個 repository 底下都會讓另外兩個看起來不需要索引。

    每個 collection 各自 try/except：其中一個因既有重複資料而建不起來時，另外兩個
    仍應建立成功。吞掉例外但留 exception log 的處理比照
    `MedicationLogRepository.ensure_indexes`——沒有唯一索引時去重與推播權搶佔都會
    失效，而那不會報錯，只會表現為重複推播，所以維運必須看得到這筆 log。
    """
    drug_news = drug_news_collection or MongoDBManager.get_drug_news_collection()
    deliveries = (
        deliveries_collection
        or MongoDBManager.get_medical_news_deliveries_collection()
    )
    shares = shares_collection or MongoDBManager.get_medical_news_shares_collection()

    try:
        await deliveries.create_index(
            [("user_id", 1), ("news_ref", 1)],
            unique=True,
            name="uniq_user_news",
        )
    except Exception:
        logger.exception(
            "[medical_news] 無法建立 medical_news_deliveries 唯一索引；"
            "多實例並存時同一則消息會重複推播給同一位使用者"
        )

    try:
        await shares.create_index(
            [("recipient_id", 1), ("news_ref", 1)],
            unique=True,
            name="uniq_recipient_news",
        )
    except Exception:
        logger.exception(
            "[medical_news] 無法建立 medical_news_shares 唯一索引；"
            "多位家人分享同一則時，同一位收件人會收到多張相同的卡"
        )

    try:
        await drug_news.create_index("url", unique=True, name="uniq_url")
        await drug_news.create_index(
            [("drug_key", 1), ("published_at", -1)],
            name="drug_key_published_at",
        )
    except Exception:
        logger.exception("[medical_news] 無法建立 drug_news 索引")


class DrugNewsRepository:
    """藥名／成分的近期官方消息。"""

    @staticmethod
    async def upsert_by_url(
        news: DrugNews, collection: Optional[Any] = None
    ) -> bool:
        """以 `url` 為鍵寫入。回傳是否為本次新建。

        用 `$set` 而非 `$setOnInsert`：同一篇公告可能被修訂（食藥署會更新內容與
        維護日期），既有文件應該跟著更新。`indexed_at` 一併更新，代表「最後一次
        確認這則消息還在」。
        """
        if collection is None:
            collection = MongoDBManager.get_drug_news_collection()

        document = news.model_dump(by_alias=True, exclude_none=True)
        document.pop("_id", None)
        result = await collection.update_one(
            {"url": news.url},
            {
                "$set": document,
                "$setOnInsert": {"_id": str(ObjectId())},
            },
            upsert=True,
        )
        return result.upserted_id is not None

    @staticmethod
    async def find_by_drug_keys(
        drug_keys: list[str],
        since: str,
        collection: Optional[Any] = None,
    ) -> list[DrugNews]:
        """撈這些藥名在 `since`（含）之後發布的消息，依發布日遞減。

        `drug_keys` 為空時直接回空list，不發查詢——沒有用藥的使用者是常態，
        讓他們每天各打一次無條件的 `$in: []` 查詢沒有意義。
        """
        if not drug_keys:
            return []
        if collection is None:
            collection = MongoDBManager.get_drug_news_collection()

        cursor = collection.find(
            {
                "drug_key": {"$in": list(drug_keys)},
                "published_at": {"$gte": since},
            }
        ).sort("published_at", -1)
        docs = await cursor.to_list(length=None)
        return [DrugNews(**{**doc, "_id": str(doc["_id"])}) for doc in docs]


class MedicalNewsDeliveryRepository:
    """某位使用者收過哪些消息卡。"""

    @staticmethod
    async def claim(
        user_id: str,
        news_ref: str,
        tier: int,
        *,
        title: str = "",
        summary: str = "",
        source_name: str = "",
        url: str = "",
        collection: Optional[Any] = None,
    ) -> bool:
        """搶下「推這則給這位使用者」的權利。插入成功回 True。

        這一支同時是去重與多實例下的原子搶佔（design.md 決策 10）。**不得改寫成
        先查再寫**：查與寫之間的空隙正是兩個排程實例同時推播的窗口。

        只接住 `DuplicateKeyError`——那代表「另一個實例先搶到了」，跳過是正確的。
        其他例外（連線中斷等）必須往上拋：把它們一併當成「已推過」會讓推播在
        資料庫異常時安靜地全部消失。
        """
        if collection is None:
            collection = MongoDBManager.get_medical_news_deliveries_collection()

        try:
            await collection.insert_one(
                {
                    "_id": str(ObjectId()),
                    "user_id": user_id,
                    "news_ref": news_ref,
                    "tier": tier,
                    "title": title,
                    "summary": summary,
                    "source_name": source_name,
                    "url": url,
                    "pushed_at": datetime.now(timezone.utc),
                    "shared_at": None,
                    "share_recipient_count": 0,
                }
            )
            return True
        except DuplicateKeyError:
            return False

    @staticmethod
    async def find(
        user_id: str,
        news_ref: str,
        collection: Optional[Any] = None,
    ) -> Optional[MedicalNewsDelivery]:
        """取回某位使用者收過的某一則消息，含當時卡片上的內容。

        分享路徑唯一的內容來源：`news_ref` 是雜湊，反解不回 url，因此不可能
        回頭去查 `drug_news`。
        """
        if collection is None:
            collection = MongoDBManager.get_medical_news_deliveries_collection()
        doc = await collection.find_one({"user_id": user_id, "news_ref": news_ref})
        if doc is None:
            return None
        return MedicalNewsDelivery(**{**doc, "_id": str(doc["_id"])})

    @staticmethod
    async def list_pushed_refs(
        user_id: str,
        since: datetime,
        collection: Optional[Any] = None,
    ) -> set[str]:
        """這位使用者在 `since` 之後收過的所有 news_ref。"""
        if collection is None:
            collection = MongoDBManager.get_medical_news_deliveries_collection()

        cursor = collection.find(
            {"user_id": user_id, "pushed_at": {"$gte": since}},
            {"news_ref": 1},
        )
        docs = await cursor.to_list(length=None)
        return {doc["news_ref"] for doc in docs if doc.get("news_ref")}

    @staticmethod
    async def mark_shared(
        user_id: str,
        news_ref: str,
        recipient_count: int,
        collection: Optional[Any] = None,
    ) -> None:
        if collection is None:
            collection = MongoDBManager.get_medical_news_deliveries_collection()

        await collection.update_one(
            {"user_id": user_id, "news_ref": news_ref},
            {
                "$set": {
                    "shared_at": datetime.now(timezone.utc),
                    "share_recipient_count": recipient_count,
                }
            },
        )

    @staticmethod
    async def count_shares_today(
        user_id: str,
        day_start: datetime,
        collection: Optional[Any] = None,
    ) -> int:
        """這位使用者今天已經分享過幾則。用於每日分享次數上限。"""
        if collection is None:
            collection = MongoDBManager.get_medical_news_deliveries_collection()

        return await collection.count_documents(
            {"user_id": user_id, "shared_at": {"$gte": day_start}}
        )


class MedicalNewsShareRepository:
    """某位收件人被分享過哪些消息。"""

    @staticmethod
    async def claim(
        recipient_id: str,
        news_ref: str,
        sharer_id: str,
        collection: Optional[Any] = None,
    ) -> bool:
        """搶下「把這則送給這位收件人」的權利。插入成功回 True。

        三位家人都按了認同時，只有第一位的送出會成立——收件人不該為同一則消息
        收到三張一樣的卡。`sharer_id` 記的是實際送成功的那一位。
        """
        if collection is None:
            collection = MongoDBManager.get_medical_news_shares_collection()

        try:
            await collection.insert_one(
                {
                    "_id": str(ObjectId()),
                    "recipient_id": recipient_id,
                    "news_ref": news_ref,
                    "sharer_id": sharer_id,
                    "sent_at": datetime.now(timezone.utc),
                }
            )
            return True
        except DuplicateKeyError:
            return False
