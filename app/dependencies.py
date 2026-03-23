import os

from app.services.gemini import GeminiService, HealthClassifier
from app.services.line.message_service import LineMessageService
from app.services.RAG.vector_search import MongoVectorSearchReader, VectorSearchConfig

mongodb_url = os.getenv("MONGODB_URL")

_gemini_service = GeminiService()
_health_classifier = HealthClassifier()
_line_message_service = LineMessageService(
    gemini_service=_gemini_service,
    health_classifier=_health_classifier,
)
_vector_search_config = VectorSearchConfig.from_settings()
_vector_search_reader = MongoVectorSearchReader(_vector_search_config)


def get_mongodb_url() -> str:
    """提供 MongoDB 連線字串做為依賴注入"""
    if not mongodb_url:
        raise ValueError("未設定 MONGODB_URL 參數")
    return mongodb_url


def get_gemini_service() -> GeminiService:
    return _gemini_service


def get_health_classifier() -> HealthClassifier:
    return _health_classifier


def get_line_message_service() -> LineMessageService:
    return _line_message_service


def get_vector_search_config() -> VectorSearchConfig:
    return _vector_search_config


def get_vector_search_reader() -> MongoVectorSearchReader:
    return _vector_search_reader
