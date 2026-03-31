from motor.motor_asyncio import AsyncIOMotorClient
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class MongoDBManager:
    _client: Optional[AsyncIOMotorClient] = None
    _mongodb_url: str = ""

    @classmethod
    def configure(cls, mongodb_url: str) -> None:
        cls._mongodb_url = mongodb_url or ""

    @classmethod
    def get_client(cls) -> AsyncIOMotorClient:
        if cls._client is None:
            mongodb_url = cls._mongodb_url.strip()
            if not mongodb_url:
                raise ValueError("未設定 MongoDB 連線字串，請先呼叫 MongoDBManager.configure()")
            logger.info("Initializing async MongoDB connection (Motor)...")
            cls._client = AsyncIOMotorClient(mongodb_url)
        return cls._client

    @classmethod
    def get_medical_collection(cls):
        client = cls.get_client()
        return client["CARE_database"]["medicalFacilities"]
