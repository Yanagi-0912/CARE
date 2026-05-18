from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Optional

from app.db.mongodb import MongoDBManager
from app.models.consultation import ConsultationSummary


class ConsultationRepository:
    @staticmethod
    async def upsert_summary(summary: ConsultationSummary) -> ConsultationSummary:
        collection = MongoDBManager.get_consultation_summaries_collection()
        payload = summary.model_dump(mode="json")
        payload["created_at"] = summary.created_at

        await collection.update_one(
            {
                "line_id": summary.line_id,
                "summary_date": summary.summary_date.isoformat(),
            },
            {"$set": payload},
            upsert=True,
        )
        return summary

    @staticmethod
    async def get_summary(
        line_id: str, target_date: date
    ) -> Optional[ConsultationSummary]:
        collection = MongoDBManager.get_consultation_summaries_collection()
        document = await collection.find_one(
            {"line_id": line_id, "summary_date": target_date.isoformat()}
        )
        if not document:
            return None
        document.pop("_id", None)
        return ConsultationSummary.model_validate(document)

    @staticmethod
    async def get_latest_summary(line_id: str) -> Optional[ConsultationSummary]:
        collection = MongoDBManager.get_consultation_summaries_collection()
        document = (
            await collection.find({"line_id": line_id})
            .sort("summary_date", -1)
            .limit(1)
            .to_list(length=1)
        )
        if not document:
            return None
        latest = document[0]
        latest.pop("_id", None)
        return ConsultationSummary.model_validate(latest)
