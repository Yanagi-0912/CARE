#  小註解：不從原有程式new物件，依賴dependency injection注入物件 那dependencies 就叫做 container
from dataclasses import dataclass
import os

import jwt  # type: ignore[import-not-found]
from fastapi import Header, HTTPException

from app.core.config import settings
from app.services.gemini import GeminiService
from app.services.agent.agent import Agent
from app.services.guardrail import GuardrailService
from app.services.rag.services import RagAnswerService
from app.services.line_messaging.message_service import LineMessageService
from app.services.line_messaging.client import LineMessagingClient, LineTokenManager
from app.services.line_messaging.event_handler import LineEventHandler
from app.services.medical.medical_service import MedicalService, medical_service
from app.tools.rag_tools import configure_rag_tool
from app.tools.medical_tools import configure_medical_tools
from app.services.vector_search import (
    MongoVectorSearchReader,
    VectorSearchConfig,
)
from app.db.mongodb import MongoDBManager
from app.db.redis import RedisManager
from app.services.users.user_profile_service import UserProfileService
from app.repositories.user_profile_repository import UserProfileRepository
from app.services.family.family_tree_service import FamilyTreeService
from app.services.liff.auth_service import LiffAuthApplicationService
from app.services.liff.jwt_service import AppJwtService
from app.services.liff.line_id_token_service import LineIdTokenService
from app.repositories.consultation_repository import ConsultationRepository
from app.services.consultation.consultation_service import ConsultationService
from app.repositories.chat_history_repository import build_chat_history_repository

_mongodb_url = os.getenv("MONGODB_URL")
MongoDBManager.configure(_mongodb_url or settings.MONGODB_URI)
_redis_url = os.getenv("REDIS_URL")
RedisManager.configure(_redis_url or settings.REDIS_URL)
_gemini_service = GeminiService(
    api_key=settings.GEMINI_API_KEY,
    model_name=settings.MODEL_NAME,
)
_guardrail_service = GuardrailService(
    async_text_to_bool=_gemini_service.invoke_boolean_structured_output,
)
_vector_search_config = VectorSearchConfig.from_settings()
_vector_search_reader = MongoVectorSearchReader(_vector_search_config)

_chat_history_repository = build_chat_history_repository()
_consultation_repository = ConsultationRepository()
_consultation_service = ConsultationService(
    chat_history_repository=_chat_history_repository,
    repository=_consultation_repository,
    gemini_service=_gemini_service,
)

_rag_answer_service = RagAnswerService(
    gemini_service=_gemini_service,
    vector_search_reader=_vector_search_reader,
)

# DI tools
configure_rag_tool(_rag_answer_service, _consultation_service)
configure_medical_tools(medical_service)

_care_agent = Agent(
    llm=_gemini_service.chat_model,
    guardrail_service=_guardrail_service,
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
    agent=_care_agent,
    line_message_service=_line_message_service,
    chat_history_repository=_chat_history_repository,
)

# 使用者資料相關的依賴注入
_user_profile_repository = UserProfileRepository()
_user_profile_service = UserProfileService(repo=_user_profile_repository)

# Family Tree 服務
_family_tree_service = FamilyTreeService(user_profile_service=_user_profile_service)

# LIFF Auth 服務
_line_id_token_service = LineIdTokenService()
_app_jwt_service = AppJwtService(
    secret=settings.AUTH_JWT_SECRET,
    algorithm=settings.AUTH_JWT_ALGORITHM,
    expires_minutes=settings.AUTH_JWT_EXPIRES_MINUTES,
)
# 提供給前端取得一個臨時token來完成下載檔案功能
_consultation_download_token_service = AppJwtService(
    secret=settings.AUTH_JWT_SECRET,
    algorithm=settings.AUTH_JWT_ALGORITHM,
    # 效期設定為5分鐘
    expires_minutes=5,
    issuer="care-consultation-download",
)
_liff_auth_application_service = LiffAuthApplicationService(
    line_id_token_service=_line_id_token_service,
    jwt_service=_app_jwt_service,
    user_profile_service=_user_profile_service,
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


def get_redis_url() -> str:
    """提供 Redis 連線字串做為依賴注入"""
    url = _redis_url or settings.REDIS_URL
    if not url:
        raise ValueError("未設定 REDIS_URL 參數")
    return url


def get_gemini_service() -> GeminiService:
    return _gemini_service


def get_guardrail_service() -> GuardrailService:
    return _guardrail_service


def get_line_message_service() -> LineMessageService:
    return _line_message_service


def get_line_event_handler() -> LineEventHandler:
    return _line_event_handler


def get_consultation_service() -> ConsultationService:
    return _consultation_service


def get_chat_history_repository():
    return _chat_history_repository


def get_line_token_manager() -> LineTokenManager:
    return _line_token_manager


def get_medical_service() -> MedicalService:
    return medical_service


def get_vector_search_config() -> VectorSearchConfig:
    return _vector_search_config


def get_vector_search_reader() -> MongoVectorSearchReader:
    return _vector_search_reader


def get_user_profile_service() -> UserProfileService:
    return _user_profile_service


def get_family_tree_service() -> FamilyTreeService:
    return _family_tree_service


def get_liff_auth_application_service() -> LiffAuthApplicationService:
    return _liff_auth_application_service


def get_consultation_download_token_service() -> AppJwtService:
    return _consultation_download_token_service


@dataclass
class CurrentUser:
    line_user_id: str


def get_current_user(authorization: str | None = Header(default=None)) -> CurrentUser:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=401, detail="Invalid Authorization header format"
        )

    try:
        line_user_id = _app_jwt_service.decode_user_id(token.strip())
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    return CurrentUser(line_user_id=line_user_id)
