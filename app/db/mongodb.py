import logging
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient


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
    def get_users_collection(cls):
        """
        取得 users collection
        """
        client = cls.get_client()
        return client["CARE_database"]["users"]

    @classmethod
    def get_medical_collection(cls):
        """
        取得 medicalFacilities collection
        """
        client = cls.get_client()
        return client["CARE_database"]["medicalFacilities"]

    @classmethod
    def get_family_tree_collection(cls):
        """
        取得 family_trees collection
        """
        client = cls.get_client()
        return client["CARE_database"]["family_trees"]

    @classmethod
    def get_pending_invitations_collection(cls):
        """
        取得 pending_invitations collection
        """
        client = cls.get_client()
        return client["CARE_database"]["pending_invitations"]

    @classmethod
    def get_consultation_summaries_collection(cls):
        """
        取得 consultation_summaries collection
        """
        client = cls.get_client()
        return client["CARE_database"]["consultation_summaries"]
