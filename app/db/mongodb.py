import logging
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import settings

logger = logging.getLogger(__name__)

class MongoDBManager:
    """
    MongoDB 管理工具
    """
    
    _mongodb_url: str = ""
    _client: Optional[AsyncIOMotorClient] = None

    @classmethod
    def configure(cls, mongodb_url: str) -> None:
        """
        設定 MongoDB_url
        """
        cls._mongodb_url = mongodb_url or ""

    @classmethod
    def get_client(cls) -> AsyncIOMotorClient:
        """
        取得 MongoDB 連線
        """
        if cls._client is None:
            mongodb_url = cls._mongodb_url.strip()
            if not mongodb_url:
                raise ValueError("未設定 MongoDB_url")
            logger.info("Initializing async MongoDB connection (Motor)...")
            cls._client = AsyncIOMotorClient(mongodb_url)
        return cls._client

    @classmethod
    def get_database(cls) -> AsyncIOMotorDatabase:
        """
        取得資料庫實例，資料庫名稱統一從 settings.MONGODB_DB 讀取，
        避免各個 collection getter 各自寫死資料庫名稱。
        """
        db_name = settings.MONGODB_DB
        if not db_name:
            raise ValueError("未設定 MONGODB_DB 參數")
        return cls.get_client()[db_name]

    @classmethod
    def get_users_collection(cls):
        """
        取得 users collection
        """
        return cls.get_database()["users"]

    @classmethod
    def get_medical_collection(cls):
        """
        取得 medicalFacilities collection
        """
        return cls.get_database()["medicalFacilities"]

    @classmethod
    def get_family_tree_collection(cls):
        """
        取得 family_trees collection
        """
        return cls.get_database()["family_trees"]

    @classmethod
    def get_pending_invitations_collection(cls):
        """
        取得 pending_invitations collection
        """
        return cls.get_database()["pending_invitations"]

    @classmethod
    def get_consultation_summaries_collection(cls):
        """
        取得 consultation_summaries collection
        """
        return cls.get_database()["consultation_summaries"]

    @classmethod
    def get_medication_reminders_collection(cls):
        """
        取得 medication_reminders collection
        """
        return cls.get_database()["medication_reminders"]

    @classmethod
    def get_medication_logs_collection(cls):
        """
        取得 medication_logs collection
        """
        return cls.get_database()["medication_logs"]

    @classmethod
    def get_medications_collection(cls):
        """
        取得 medications collection
        """
        return cls.get_database()["medications"]

    @classmethod
    def get_prescription_drafts_collection(cls):
        """
        取得 prescription_drafts collection
        """
        return cls.get_database()["prescription_drafts"]

    @classmethod
    def get_knowledge_reports_collection(cls):
        """
        取得 knowledge_reports collection
        """
        return cls.get_database()["knowledge_reports"]

