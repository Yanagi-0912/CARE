#  小註解：不從原有程式new物件，依賴dependency injection注入物件 那dependencies 就叫做 container
import os

from app.services.gemini import GeminiService, HealthClassifier
from app.orchestration import ResponseOrchestrator
from app.services.guardrail import GuardrailService
from app.services.RAG.retrieval import RagAnswerService
from app.services.line.message_service import LineMessageService
from app.services.RAG.shared.vector_search import (
    MongoVectorSearchReader,
    VectorSearchConfig,
)

mongodb_url = os.getenv("MONGODB_URL")

_gemini_service = GeminiService()
_health_classifier = HealthClassifier(gemini_service=_gemini_service)
_guardrail_service = GuardrailService(gemini_service=_gemini_service)
_vector_search_config = VectorSearchConfig.from_settings()
_vector_search_reader = MongoVectorSearchReader(_vector_search_config)
_rag_answer_service = RagAnswerService(
    gemini_service=_gemini_service,
    vector_search_reader=_vector_search_reader,
)
_response_orchestrator = ResponseOrchestrator(
    gemini_service=_gemini_service,
    guardrail_service=_guardrail_service,
    rag_answer_service=_rag_answer_service,
)
_line_message_service = LineMessageService(
    response_orchestrator=_response_orchestrator,
)


def get_mongodb_url() -> str:
    """提供 MongoDB 連線字串做為依賴注入"""
    if not mongodb_url:
        raise ValueError("未設定 MONGODB_URL 參數")
    return mongodb_url


def get_gemini_service() -> GeminiService:
    return _gemini_service


def get_health_classifier() -> HealthClassifier:
    return _health_classifier


def get_guardrail_service() -> GuardrailService:
    return _guardrail_service


def get_line_message_service() -> LineMessageService:
    return _line_message_service


def get_vector_search_config() -> VectorSearchConfig:
    return _vector_search_config


def get_vector_search_reader() -> MongoVectorSearchReader:
    return _vector_search_reader
