#  小註解：不從原有程式new物件，依賴dependency injection注入物件 那dependencies 就叫做 container
import os
from dataclasses import dataclass

# 第三方套件
import jwt  # type: ignore[import-not-found]
from fastapi import Header, HTTPException

# 專案核心設定
from app.core.config import settings
from app.db.mongodb import MongoDBManager
from app.db.redis import RedisManager

# 專案模型、倉庫與工具
from app.repositories.consultation_repository import ConsultationRepository
from app.repositories.user_profile_repository import UserProfileRepository
from app.tools.medical_tools import configure_medical_tools
from app.tools.rag_tools import configure_rag_tool

# 專案各項服務
from app.services.agent.agent import Agent
from app.services.consultation.consultation_service import ConsultationService
from app.services.consultation.proxies import (
    ConsultationAwareAgent,
    ConsultationAwareLineMessageService,
)
from app.services.consultation.store import build_consultation_store
from app.services.family.family_tree_service import FamilyTreeService
from app.services.gemini import GeminiService
from app.services.guardrail import GuardrailService
from app.services.liff.auth_service import LiffAuthApplicationService
from app.services.liff.jwt_service import AppJwtService
from app.services.liff.line_id_token_service import LineIdTokenService
from app.services.line_messaging.client import LineMessagingClient, LineTokenManager
from app.services.line_messaging.event_handler import LineEventHandler
from app.services.line_messaging.message_service import LineMessageService
from app.services.medical.medical_service import MedicalService, medical_service
from app.services.rag.services import RagAnswerService
from app.services.users.user_profile_service import UserProfileService
from app.services.vector_search import (
    MongoVectorSearchReader,
    VectorSearchConfig,
)

# ==============================================================================
# 1. 資料庫連線配置 (Database Initialization)
# ==============================================================================
MongoDBManager.configure(settings.MONGODB_URI)

RedisManager.configure(settings.REDIS_URL)

# ==============================================================================
# 2. 核心基礎服務 (Core Infrastructure Services)
# ==============================================================================
_gemini_service = GeminiService(
    api_key=settings.GEMINI_API_KEY,
    model_name=settings.MODEL_NAME,
)

_guardrail_service = GuardrailService(
    async_text_to_bool=_gemini_service.invoke_boolean_structured_output,
)

# ==============================================================================
# 3. 向量檢索與 RAG 服務 (Vector Search & RAG Services)
# ==============================================================================
_vector_search_config = VectorSearchConfig.from_settings()
_vector_search_reader = MongoVectorSearchReader(_vector_search_config)

_rag_answer_service = RagAnswerService(
    gemini_service=_gemini_service,
    vector_search_reader=_vector_search_reader,
)

# ==============================================================================
# 4. 諮詢管理服務 (Consultation Services)
# ==============================================================================
_consultation_store = build_consultation_store()
_consultation_repository = ConsultationRepository()
_consultation_service = ConsultationService(
    store=_consultation_store,
    repository=_consultation_repository,
    gemini_service=_gemini_service,
)

# ==============================================================================
# 5. 工具配置 (Tools Configuration)
# ==============================================================================
configure_rag_tool(_rag_answer_service, _consultation_service)
configure_medical_tools(medical_service)

# ==============================================================================
# 6. 核心 Agent 與 LINE 整合服務 (Agent & LINE Integration)
# ==============================================================================
_care_agent = Agent(
    llm=_gemini_service._chat_llm,
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

# 帶有諮詢歷史上下文的 Proxy 服務
_consultation_aware_agent = ConsultationAwareAgent(
    agent=_care_agent,
    consultation_service=_consultation_service,
)

_consultation_aware_line_message_service = ConsultationAwareLineMessageService(
    service=_line_message_service,
    consultation_service=_consultation_service,
)

_line_event_handler = LineEventHandler(
    agent=_consultation_aware_agent,
    line_message_service=_consultation_aware_line_message_service,
)

# ==============================================================================
# 7. 使用者管理、家譜與 LIFF 認證服務 (User, Family & LIFF Auth)
# ==============================================================================
_user_profile_repository = UserProfileRepository()
_user_profile_service = UserProfileService(repo=_user_profile_repository)

_family_tree_service = FamilyTreeService(user_profile_service=_user_profile_service)

_line_id_token_service = LineIdTokenService()

# JWT 權限驗證服務
_app_jwt_service = AppJwtService(
    secret=settings.AUTH_JWT_SECRET,
    algorithm=settings.AUTH_JWT_ALGORITHM,
    expires_minutes=settings.AUTH_JWT_EXPIRES_MINUTES,
)

# 下載臨時 token 服務（5分鐘效期）
_consultation_download_token_service = AppJwtService(
    secret=settings.AUTH_JWT_SECRET,
    algorithm=settings.AUTH_JWT_ALGORITHM,
    expires_minutes=5,
    issuer="care-consultation-download",
)

_liff_auth_application_service = LiffAuthApplicationService(
    line_id_token_service=_line_id_token_service,
    jwt_service=_app_jwt_service,
    user_profile_service=_user_profile_service,
)

# ==============================================================================
# 8. FastAPI 依賴注入取得器 (Dependency Getters)
# ==============================================================================

def get_mongodb_uri() -> str:
    """提供 MongoDB 連線字串做為依賴注入"""
    uri = settings.MONGODB_URI
    if not uri:
        raise ValueError("未設定 MONGODB_URI 參數")
    return uri


def get_redis_url() -> str:
    """提供 Redis 連線字串做為依賴注入"""
    url = settings.REDIS_URL
    if not url:
        raise ValueError("未設定 REDIS_URL 參數")
    return url


def get_gemini_service() -> GeminiService:
    """取得 GeminiService 實例"""
    return _gemini_service


def get_guardrail_service() -> GuardrailService:
    """取得 GuardrailService 實例"""
    return _guardrail_service


def get_line_message_service() -> LineMessageService:
    """取得 LineMessageService 實例"""
    return _line_message_service


def get_line_event_handler() -> LineEventHandler:
    """取得 LineEventHandler 實例"""
    return _line_event_handler


def get_consultation_service() -> ConsultationService:
    """取得 ConsultationService 實例"""
    return _consultation_service


def get_consultation_store():
    """取得 ConsultationStore 實例"""
    return _consultation_store


def get_line_token_manager() -> LineTokenManager:
    """取得 LineTokenManager 實例"""
    return _line_token_manager


def get_medical_service() -> MedicalService:
    """取得 MedicalService 實例"""
    return medical_service


def get_vector_search_config() -> VectorSearchConfig:
    """取得 VectorSearchConfig 實例"""
    return _vector_search_config


def get_vector_search_reader() -> MongoVectorSearchReader:
    """取得 MongoVectorSearchReader 實例"""
    return _vector_search_reader


def get_user_profile_service() -> UserProfileService:
    """取得 UserProfileService 實例"""
    return _user_profile_service


def get_family_tree_service() -> FamilyTreeService:
    """取得 FamilyTreeService 實例"""
    return _family_tree_service


def get_liff_auth_application_service() -> LiffAuthApplicationService:
    """取得 LiffAuthApplicationService 實例"""
    return _liff_auth_application_service


def get_consultation_download_token_service() -> AppJwtService:
    """取得下載檔案專用臨時 token 服務"""
    return _consultation_download_token_service


# ==============================================================================
# 9. 身分驗證依賴 (User Authentication Dependency)
# ==============================================================================

@dataclass
class CurrentUser:
    """當前登入的使用者資訊"""
    line_user_id: str


def get_current_user(authorization: str | None = Header(default=None)) -> CurrentUser:
    """
    從 Authorization Header 解析 JWT，取得目前使用者。
    """
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
