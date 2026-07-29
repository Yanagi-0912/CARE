from dataclasses import dataclass

import jwt  # type: ignore[import-not-found]
from fastapi import Depends, Header, HTTPException
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.core.config import settings
from app.db.mongodb import MongoDBManager
from app.db.redis import RedisManager
from app.repositories.chat_history_repository import build_chat_history_repository
from app.repositories.consultation_repository import ConsultationRepository
from app.repositories.user_profile_repository import UserProfileRepository
from app.services.agent.agent import Agent
from app.services.consultation.consultation_service import ConsultationService
from app.services.family.family_tree_service import FamilyTreeService
from app.services.medication.medication_service import MedicationService
from app.services.medication.medication_scheduler import start_medication_scheduler
from app.services.gemini import GeminiService
from app.services.guardrail import GuardrailService
from app.services.history.history_service import LineMessageHistoryService
from app.services.liff.auth_service import LiffAuthApplicationService
from app.services.liff.jwt_service import AppJwtService
from app.services.liff.line_id_token_service import LineIdTokenService
from app.services.liff.line_language_service import LineLanguageService
from app.services.line_messaging.event_handler import LineEventHandler
from app.services.line_messaging.handler.location_handler import LineLocationHandler
from app.services.line_messaging.handler.media_handler import LineMediaHandler
from app.services.line_messaging.handler.message_handler import LineMessageHandler
from app.services.line_messaging.loading_animation import LineLoadingAnimationService
from app.services.line_messaging.reply.reply import LineReplier
from app.services.line_messaging.reply.tts_service import TTSService
from app.services.line_messaging.token_manager import LineTokenManager
from app.services.medical.medical_service import MedicalService, medical_service
from app.services.rag import MongoAtlasVectorRetriever, RETRIEVAL_TOP_K, RagAnswerService
from app.services.rag.firecrawl_client import FirecrawlClient
from app.services.users.user_profile_service import UserProfileService
from app.tools.medical_tools import configure_medical_tools
from app.tools.rag_tools import configure_rag_tool

MongoDBManager.configure(settings.MONGODB_URI)
RedisManager.configure(settings.REDIS_URL)

_gemini_service = GeminiService(
    api_key=settings.GEMINI_API_KEY,
    model_name=settings.MODEL_NAME,
)

_guardrail_service = GuardrailService(
    async_text_to_bool=_gemini_service.invoke_boolean_structured_output,
)

_query_embeddings_kwargs: dict = {
    "model": settings.EMBEDDING_MODEL,
    "google_api_key": settings.GEMINI_API_KEY,
    "task_type": "RETRIEVAL_QUERY",
}
if settings.MONGODB_VECTOR_DIM > 0:
    _query_embeddings_kwargs["output_dimensionality"] = settings.MONGODB_VECTOR_DIM
_query_embeddings = GoogleGenerativeAIEmbeddings(**_query_embeddings_kwargs)

_rag_retriever = MongoAtlasVectorRetriever(
    embeddings=_query_embeddings,
    mongo_uri=settings.MONGODB_URI,
    db_name=settings.MONGODB_DB,
    collection_name=settings.MONGODB_COLLECTION,
    index_name=settings.MONGODB_VECTOR_INDEX,
    vector_field=settings.MONGODB_VECTOR_FIELD,
    text_field=settings.MONGODB_TEXT_FIELD,
    vector_dim=settings.MONGODB_VECTOR_DIM if settings.MONGODB_VECTOR_DIM > 0 else None,
    k=RETRIEVAL_TOP_K,
)

_firecrawl_client = None
if settings.FIRECRAWL_API_KEY:
    _firecrawl_client = FirecrawlClient(api_key=settings.FIRECRAWL_API_KEY)

_rag_answer_service = RagAnswerService(
    gemini_service=_gemini_service,
    retriever=_rag_retriever,
    web_client=_firecrawl_client,
)

_chat_history_repository = build_chat_history_repository()
_consultation_repository = ConsultationRepository()
_consultation_service = ConsultationService(
    chat_history_repository=_chat_history_repository,
    repository=_consultation_repository,
    gemini_service=_gemini_service,
)

configure_rag_tool(_rag_answer_service)
configure_medical_tools(medical_service)

_care_agent = Agent(
    llm=_gemini_service.chat_model,
    guardrail_service=_guardrail_service,
)

_line_history_service = LineMessageHistoryService(_chat_history_repository)

_line_token_manager = LineTokenManager(
    channel_id=settings.LINE_CHANNEL_ID,
    channel_secret=settings.LINE_CHANNEL_SECRET,
)

_line_loading_animation_service = LineLoadingAnimationService(_line_token_manager)

_user_profile_repository = UserProfileRepository()
_user_profile_service = UserProfileService(repo=_user_profile_repository)
_tts_service = TTSService()

_line_replier = LineReplier(
    token_manager=_line_token_manager,
    tts_service=_tts_service,
)
_message_handler = LineMessageHandler(
    agent=_care_agent,
    history_service=_line_history_service,
    user_profile_service=_user_profile_service,
    replier=_line_replier,
    loading_animation_service=_line_loading_animation_service,
)
_media_handler = LineMediaHandler(
    agent=_care_agent,
    history_service=_line_history_service,
    user_profile_service=_user_profile_service,
    replier=_line_replier,
    loading_animation_service=_line_loading_animation_service,
)
_location_handler = LineLocationHandler(
    agent=_care_agent,
    history_service=_line_history_service,
    user_profile_service=_user_profile_service,
    replier=_line_replier,
    loading_animation_service=_line_loading_animation_service,
)
_family_tree_service = FamilyTreeService()
_medication_service = MedicationService()

_line_event_handler = LineEventHandler(
    message_handler=_message_handler,
    media_handler=_media_handler,
    location_handler=_location_handler,
    replier=_line_replier,
    medication_service=_medication_service,
)


_line_id_token_service = LineIdTokenService()

_app_jwt_service = AppJwtService(
    secret=settings.AUTH_JWT_SECRET,
    algorithm=settings.AUTH_JWT_ALGORITHM,
    expires_minutes=settings.AUTH_JWT_EXPIRES_MINUTES,
)

_consultation_download_token_service = AppJwtService(
    secret=settings.AUTH_JWT_SECRET,
    algorithm=settings.AUTH_JWT_ALGORITHM,
    expires_minutes=5,
    issuer="care-consultation-download",
)

_line_language_service = LineLanguageService(
    get_access_token=_line_token_manager.get_token,
)

_liff_auth_application_service = LiffAuthApplicationService(
    line_id_token_service=_line_id_token_service,
    jwt_service=_app_jwt_service,
    user_profile_service=_user_profile_service,
    line_language_service=_line_language_service,
)


def get_mongodb_uri() -> str:
    uri = settings.MONGODB_URI
    if not uri:
        raise ValueError("MONGODB_URI is not configured")
    return uri


def get_redis_url() -> str:
    url = settings.REDIS_URL
    if not url:
        raise ValueError("REDIS_URL is not configured")
    return url


def get_gemini_service() -> GeminiService:
    return _gemini_service


def get_guardrail_service() -> GuardrailService:
    return _guardrail_service


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


def get_query_embeddings() -> GoogleGenerativeAIEmbeddings:
    """取得 RAG query embeddings 實例"""
    return _query_embeddings


def get_rag_retriever() -> MongoAtlasVectorRetriever:
    """取得 MongoDB Atlas 向量檢索 retriever"""
    return _rag_retriever


def get_user_profile_service() -> UserProfileService:
    return _user_profile_service


def get_tts_service() -> TTSService:
    return _tts_service


def get_family_tree_service() -> FamilyTreeService:
    return _family_tree_service


def get_medication_service() -> MedicationService:
    return _medication_service


def get_line_replier() -> LineReplier:
    return _line_replier


def get_user_profile_service() -> UserProfileService:
    return _user_profile_service




def get_liff_auth_application_service() -> LiffAuthApplicationService:
    return _liff_auth_application_service


def get_consultation_download_token_service() -> AppJwtService:
    return _consultation_download_token_service


def get_jwt_service() -> AppJwtService:
    return _app_jwt_service


@dataclass
class CurrentUser:
    line_user_id: str


def get_current_user(
    authorization: str | None = Header(default=None),
    jwt_service: AppJwtService = Depends(get_jwt_service),
) -> CurrentUser:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header format",
        )

    try:
        line_user_id = jwt_service.decode_user_id(token.strip())
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    return CurrentUser(line_user_id=line_user_id)
