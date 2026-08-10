from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.db.mongodb import MongoDBManager
from app.models.knowledge_report import IngestJob, KnowledgeReport


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
        # admin 待審佇列：依 status 篩選、created_at 倒序分頁
        await collection.create_index(
            [("status", 1), ("created_at", -1)],
            name="knowledge_report_status_created",
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
        statuses: list[str],
        collection: Optional[Any] = None,
        *,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[KnowledgeReport]:
        if collection is None:
            collection = MongoDBManager.get_knowledge_reports_collection()

        if not statuses:
            return []

        cursor = collection.find({"status": {"$in": statuses}}).sort(
            "created_at", -1
        )
        if offset:
            cursor = cursor.skip(offset)
        if limit is not None:
            cursor = cursor.limit(limit)
        documents = await cursor.to_list(length=limit)
        reports: list[KnowledgeReport] = []
        for document in documents:
            document.pop("_id", None)
            reports.append(KnowledgeReport.model_validate(document))
        return reports

    @staticmethod
    async def count_by_statuses(
        statuses: list[str], collection: Optional[Any] = None
    ) -> int:
        if collection is None:
            collection = MongoDBManager.get_knowledge_reports_collection()

        if not statuses:
            return 0

        return int(await collection.count_documents({"status": {"$in": statuses}}))

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
    def _no_live_ingest_job(stale_before: datetime) -> dict[str, Any]:
        """「沒有進行中的新鮮 ingest 工作」的查詢條件。

        status 為 None 的是舊紀錄，一律視為已結束；started_at 早於 stale_before
        的視為服務重啟遺留的孤兒，可被取代。
        """
        return {
            "$or": [
                {"ingest_job": None},
                {"ingest_job.status": {"$ne": "running"}},
                {"ingest_job.started_at": {"$lt": stale_before}},
            ]
        }

    @staticmethod
    async def start_ingest_job(
        *,
        report_id: str,
        job: IngestJob,
        stale_before: datetime,
        resolution: Optional[str] = None,
        reviewer_note: Optional[str] = None,
        collection: Optional[Any] = None,
    ) -> bool:
        """原子地登記一份 ingest 工作。回傳是否成功取得登記。

        未命中代表回報已結案，或已有另一份進行中的新鮮工作。
        resolution／reviewer_note 為 None 時保留原值（patch 語意）。
        """
        if collection is None:
            collection = MongoDBManager.get_knowledge_reports_collection()

        payload: dict[str, Any] = {
            # 刻意用 model_dump() 而非 model_dump(mode="json")：後者會把
            # started_at／finished_at 序列化成 ISO 字串，而 finish_ingest_job
            # 的 filter 與 _no_live_ingest_job 的 $lt 都拿 datetime 去比。
            # Mongo 不做跨型別相等比較，且 BSON 型別排序下 String 恆大於 Date，
            # 兩者都會永遠不成立——結果是工作永遠停在 running、逾時的孤兒工作
            # 也永遠無法被取代。存成 BSON date 才能讓兩個判定真的生效。
            "status": "reviewing",
            "ingest_job": job.model_dump(),
            "updated_at": job.started_at,
        }
        if resolution is not None:
            payload["resolution"] = resolution
        if reviewer_note is not None:
            payload["reviewer_note"] = reviewer_note

        result = await collection.update_one(
            {
                "report_id": report_id,
                "status": {"$nin": ["resolved", "rejected"]},
                **KnowledgeReportRepository._no_live_ingest_job(stale_before),
            },
            {"$set": payload},
        )
        return int(getattr(result, "matched_count", 0) or 0) > 0

    @staticmethod
    async def finish_ingest_job(
        *,
        report_id: str,
        started_at: datetime,
        report_status: str,
        job: IngestJob,
        collection: Optional[Any] = None,
    ) -> bool:
        """寫回 ingest 結果，僅在工作仍是本次啟動的那一份時套用。

        filter 綁 started_at，期間若被拒絕或被重新 approve 就不會命中，
        結果直接丟棄——避免用工作開始時的舊快照蓋掉其他人的變更。
        """
        if collection is None:
            collection = MongoDBManager.get_knowledge_reports_collection()

        result = await collection.update_one(
            {
                "report_id": report_id,
                "ingest_job.status": "running",
                "ingest_job.started_at": started_at,
            },
            {
                "$set": {
                    "status": report_status,
                    # 與 start_ingest_job 一致存成 BSON date，見該處註解
                    "ingest_job": job.model_dump(),
                    "updated_at": job.finished_at,
                }
            },
        )
        return int(getattr(result, "matched_count", 0) or 0) > 0

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
