from __future__ import annotations

from typing import Any, Optional

from app.db.mongodb import MongoDBManager
from app.models.knowledge_report import KnowledgeReport


class KnowledgeReportRepository:
    @staticmethod
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        if collection is None:
            collection = MongoDBManager.get_knowledge_reports_collection()

        await collection.create_index(
            [("report_id", 1)],
            name="knowledge_report_id",
            unique=True,
        )
        await collection.create_index(
            [("line_user_id", 1), ("created_at", -1)],
            name="knowledge_report_line_user_created",
        )

    @staticmethod
    async def insert(
        report: KnowledgeReport, collection: Optional[Any] = None
    ) -> KnowledgeReport:
        if collection is None:
            collection = MongoDBManager.get_knowledge_reports_collection()

        payload = report.model_dump(mode="json")
        payload["created_at"] = report.created_at
        payload["updated_at"] = report.updated_at
        await collection.insert_one(payload)
        return report

    @staticmethod
    async def find_by_report_id(
        report_id: str, collection: Optional[Any] = None
    ) -> Optional[KnowledgeReport]:
        if collection is None:
            collection = MongoDBManager.get_knowledge_reports_collection()

        document = await collection.find_one({"report_id": report_id})
        if not document:
            return None
        document.pop("_id", None)
        return KnowledgeReport.model_validate(document)

    @staticmethod
    async def list_by_line_user_id(
        line_user_id: str, collection: Optional[Any] = None
    ) -> list[KnowledgeReport]:
        if collection is None:
            collection = MongoDBManager.get_knowledge_reports_collection()

        cursor = collection.find({"line_user_id": line_user_id}).sort(
            "created_at", -1
        )
        documents = await cursor.to_list(length=None)
        reports: list[KnowledgeReport] = []
        for document in documents:
            document.pop("_id", None)
            reports.append(KnowledgeReport.model_validate(document))
        return reports

    @staticmethod
    async def list_by_statuses(
        statuses: list[str], collection: Optional[Any] = None
    ) -> list[KnowledgeReport]:
        if collection is None:
            collection = MongoDBManager.get_knowledge_reports_collection()

        if not statuses:
            return []

        cursor = collection.find({"status": {"$in": statuses}}).sort(
            "created_at", -1
        )
        documents = await cursor.to_list(length=None)
        reports: list[KnowledgeReport] = []
        for document in documents:
            document.pop("_id", None)
            reports.append(KnowledgeReport.model_validate(document))
        return reports

    @staticmethod
    async def update(
        report: KnowledgeReport, collection: Optional[Any] = None
    ) -> KnowledgeReport:
        if collection is None:
            collection = MongoDBManager.get_knowledge_reports_collection()

        payload = report.model_dump(mode="json")
        payload["created_at"] = report.created_at
        payload["updated_at"] = report.updated_at
        await collection.update_one(
            {"report_id": report.report_id},
            {"$set": payload},
        )
        return report

    @staticmethod
    async def delete_pending_or_reviewing_by_urls(
        urls: list[str], collection: Optional[Any] = None
    ) -> int:
        if collection is None:
            collection = MongoDBManager.get_knowledge_reports_collection()

        if not urls:
            return 0

        result = await collection.delete_many(
            {
                "status": {"$in": ["pending", "reviewing"]},
                "user_source_urls": {"$in": urls},
            }
        )
        return int(getattr(result, "deleted_count", 0) or 0)
