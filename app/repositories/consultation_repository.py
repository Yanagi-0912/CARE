from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Optional

from app.db.mongodb import MongoDBManager
from app.models.consultation import ConsultationSummary

logger = logging.getLogger(__name__)


class ConsultationRepository:
    @staticmethod
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        if collection is None:
            collection = MongoDBManager.get_consultation_summaries_collection()

        # 嘗試防禦性地刪除舊的 TTL 索引，若不存在或權限不足則忽略
        try:
            await collection.drop_index("consultation_summary_created_at_ttl")
            logger.info(
                "Successfully dropped index: consultation_summary_created_at_ttl"
            )
        except Exception as e:
            logger.info(
                f"Index consultation_summary_created_at_ttl drop skipped or not found: {e}"
            )

        # 建立複合索引以加速依使用者與日期的檢索與排序
        await collection.create_index(
            [("line_id", 1), ("summary_date", -1)],
            name="consultation_summary_line_id_date",
        )

    @staticmethod
    async def upsert_summary(
        summary: ConsultationSummary, collection: Optional[Any] = None
    ) -> ConsultationSummary:
        if collection is None:
            collection = MongoDBManager.get_consultation_summaries_collection()

        payload = summary.model_dump(mode="json")
        payload["created_at"] = summary.created_at

        # 更新或插入最新的摘要
        await collection.update_one(
            {
                "line_id": summary.line_id,
                "summary_date": summary.summary_date.isoformat(),
            },
            {"$set": payload},
            upsert=True,
        )

        # 限制每位使用者最多只保留最新的 20 筆摘要，自動擠掉最舊的
        cursor = collection.find({"line_id": summary.line_id}, {"_id": 1}).sort(
            "summary_date", -1
        )
        docs = await cursor.to_list(length=None)
        # 如果超過 20 筆，刪除最舊的
        if len(docs) > 20:
            old_ids = [doc["_id"] for doc in docs[20:]]
            await collection.delete_many({"_id": {"$in": old_ids}})

        return summary

    @staticmethod
    async def get_summary(
        line_id: str, target_date: date, collection: Optional[Any] = None
    ) -> Optional[ConsultationSummary]:
        if collection is None:
            collection = MongoDBManager.get_consultation_summaries_collection()

        document = await collection.find_one(
            {"line_id": line_id, "summary_date": target_date.isoformat()}
        )
        if not document:
            return None
        document.pop("_id", None)
        return ConsultationSummary.model_validate(document)

    @staticmethod
    async def get_latest_summary(
        line_id: str, collection: Optional[Any] = None
    ) -> Optional[ConsultationSummary]:
        if collection is None:
            collection = MongoDBManager.get_consultation_summaries_collection()

        cursor = collection.find({"line_id": line_id}).sort("summary_date", -1)
        document = await cursor.limit(1).to_list(length=1)
        if not document:
            return None
        latest = document[0]
        latest.pop("_id", None)
        return ConsultationSummary.model_validate(latest)

    @staticmethod
    async def get_all_summaries(
        line_id: str, collection: Optional[Any] = None
    ) -> list[ConsultationSummary]:
        if collection is None:
            collection = MongoDBManager.get_consultation_summaries_collection()

        cursor = collection.find({"line_id": line_id}).sort("summary_date", -1)
        documents = await cursor.to_list(length=None)
        summaries: list[ConsultationSummary] = []
        for document in documents:
            document.pop("_id", None)
            summaries.append(ConsultationSummary.model_validate(document))
        return summaries
