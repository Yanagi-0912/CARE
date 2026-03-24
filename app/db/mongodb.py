import os
from motor.motor_asyncio import AsyncIOMotorClient
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class MongoDBManager:
    _client: Optional[AsyncIOMotorClient] = None
    
    @classmethod
    def get_client(cls) -> AsyncIOMotorClient:
        if cls._client is None:
            from app.dependencies import get_mongodb_url
            mongodb_url = get_mongodb_url()
            logger.info("Initializing async MongoDB connection (Motor)...")
            cls._client = AsyncIOMotorClient(mongodb_url)
        return cls._client

    @classmethod
    def get_medical_collection(cls):
        client = cls.get_client()
        return client["CARE_database"]["medicalFacilities"]
