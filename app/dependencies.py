#  小註解：不從原有程式new物件，依賴dependency injection注入物件 那dependencies 就叫做 container
import os

from app.core.config import settings
from app.infrastructure.gemini import GeminiService
from app.application.orchestration import ResponseOrchestrator
from app.application.guardrail import GuardrailService
from app.application.rag.services import RagAnswerService
from app.infrastructure.line.message_service import LineMessageService
from app.infrastructure.line.client import LineMessagingClient, LineTokenManager
from app.application.line.event_handler import LineEventHandler
from app.application.medical.medical_service import MedicalService, medical_service
from app.infrastructure.vector_search import (
    MongoVectorSearchReader,
    VectorSearchConfig,
)

_mongodb_url = os.getenv("MONGODB_URL")

_gemini_service = GeminiService(
    api_key=settings.GEMINI_API_KEY,
    model_name=settings.MODEL_NAME,
)
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

_line_token_manager = LineTokenManager(
    channel_id=settings.LINE_CHANNEL_ID,
    channel_secret=settings.LINE_CHANNEL_SECRET,
)

_line_message_service = LineMessageService(
    token_provider=_line_token_manager,
    medical_service=medical_service,
    line_messaging_client=LineMessagingClient(),
)

_line_event_handler = LineEventHandler(
    response_orchestrator=_response_orchestrator,
    line_message_service=_line_message_service,
    medical_service=medical_service,
)


def get_mongodb_url() -> str:
    """提供 MongoDB 連線字串做為依賴注入"""
    # 相容兩種環境變數命名：
    # - MONGODB_URL（舊命名）
    # - MONGODB_URI（config.py 目前使用）
    url = _mongodb_url or settings.MONGODB_URI
    if not url:
        raise ValueError("未設定 MONGODB_URL（或 MONGODB_URI）參數")
    return url


def get_gemini_service() -> GeminiService:
    return _gemini_service


def get_guardrail_service() -> GuardrailService:
    return _guardrail_service


def get_line_message_service() -> LineMessageService:
    return _line_message_service


def get_line_event_handler() -> LineEventHandler:
    return _line_event_handler


def get_line_token_manager() -> LineTokenManager:
    return _line_token_manager


def get_medical_service() -> MedicalService:
    return medical_service


def get_vector_search_config() -> VectorSearchConfig:
    return _vector_search_config


def get_vector_search_reader() -> MongoVectorSearchReader:
    return _vector_search_reader
