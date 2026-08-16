from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.db.mongodb import MongoDBManager
from app.models.knowledge_report import ContentPreview


class KnowledgeReportPreviewRepository:
    @staticmethod
    async def ensure_indexes(collection: Optional[Any] = None) -> None:
        if collection is None:
            collection = MongoDBManager.get_knowledge_report_previews_collection()

        # report_id 唯一：同一筆回報只留最新一份預覽，upsert_for_report 的整份
        # 取代靠這個索引保證不會因為併發而留下兩份。
        await collection.create_index(
            [("report_id", 1)],
            name="knowledge_report_preview_report_id",
            unique=True,
        )
        # expireAfterSeconds=0 讓 Mongo 以 expires_at 欄位本身的時間作為到期點，
        # 而不是「建立後 N 秒」。少了這個關鍵字就只是一個普通索引，快照不會被回收。
        await collection.create_index(
            [("expires_at", 1)],
            name="knowledge_report_preview_ttl",
            expireAfterSeconds=0,
        )

    @staticmethod
    async def upsert_for_report(
        preview: ContentPreview, collection: Optional[Any] = None
    ) -> ContentPreview:
        """以 report_id 為鍵整份取代該回報的預覽。"""
        if collection is None:
            collection = MongoDBManager.get_knowledge_report_previews_collection()

        # 刻意用 model_dump() 而非 model_dump(mode="json")：後者會把 created_at／
        # expires_at 序列化成 ISO 字串。字串型別的 expires_at 會被 Mongo 的 TTL
        # monitor 略過（快照永不回收），而 find_ready 的 $gt 比較在 BSON 型別排序
        # 下 String 恆大於 Date，已逾期的快照會永遠被判定為仍然有效。
        payload = preview.model_dump()
        await collection.replace_one(
            {"report_id": preview.report_id},
            payload,
            upsert=True,
        )
        return preview

    @staticmethod
    async def finish(
        preview: ContentPreview, collection: Optional[Any] = None
    ) -> bool:
        """寫回抓取結果，僅在該回報的預覽仍是本次這一份時套用。

        filter 綁 preview_id，期間若 admin 按了重新抓取而產生新預覽就不會命中，
        本次結果直接丟棄——避免用舊的抓取結果蓋掉較新的那一份，那正是核准綁定
        要擋的「看的是 v2、進庫的是 v1」。不 upsert：預覽已消失就不該復活。
        """
        if collection is None:
            collection = MongoDBManager.get_knowledge_report_previews_collection()

        result = await collection.replace_one(
            {"report_id": preview.report_id, "preview_id": preview.preview_id},
            preview.model_dump(),
        )
        return int(getattr(result, "matched_count", 0) or 0) > 0

    @staticmethod
    async def find_by_report_id(
        report_id: str, collection: Optional[Any] = None
    ) -> Optional[ContentPreview]:
        """取該回報目前的預覽，不論狀態與是否逾期。

        逾期判定刻意留給呼叫端：TTL monitor 每 60 秒才跑一次，逾期文件在被實際
        刪除前仍查得到；而核准端要能分辨「逾期」與「查無預覽」才給得出正確的
        409 訊息。
        """
        if collection is None:
            collection = MongoDBManager.get_knowledge_report_previews_collection()

        document = await collection.find_one({"report_id": report_id})
        if not document:
            return None
        document.pop("_id", None)
        return ContentPreview.model_validate(document)

    @staticmethod
    async def find_ready(
        report_id: str,
        *,
        now: datetime,
        collection: Optional[Any] = None,
    ) -> Optional[ContentPreview]:
        """取該回報未逾期且已就緒的預覽；供 TTL 內的冪等判斷使用。"""
        if collection is None:
            collection = MongoDBManager.get_knowledge_report_previews_collection()

        document = await collection.find_one(
            {
                "report_id": report_id,
                "status": "ready",
                "expires_at": {"$gt": now},
            }
        )
        if not document:
            return None
        document.pop("_id", None)
        return ContentPreview.model_validate(document)
