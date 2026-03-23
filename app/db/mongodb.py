import os
from motor.motor_asyncio import AsyncIOMotorClient
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class MongoDBManager:
    _client: Optional[AsyncIOMotorClient] = None
    
    @classmethod
    def get_client(cls) -> AsyncIOMotorClient:
        """
        取得唯一的 AsyncIOMotorClient (Singleton)。
        如果尚未連線則自動嘗試連線。
        """
        if cls._client is None:
            mongodb_url = os.getenv("MONGODB_URL")
            if not mongodb_url:
                raise ValueError("未設定 MONGODB_URL")
            logger.info("Initializing async MongoDB connection (Motor)...")
            cls._client = AsyncIOMotorClient(mongodb_url)
        return cls._client

    @classmethod
    def get_medical_collection(cls):
        """
        取得醫療機構資料集合 (Collection)
        """
        client = cls.get_client()
        return client["CARE_database"]["medicalFacilities"]
