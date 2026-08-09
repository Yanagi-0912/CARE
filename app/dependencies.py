from dataclasses import dataclass
import logging

import jwt  # type: ignore[import-not-found]
from fastapi import Depends, Header, HTTPException
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.core.config import settings

logger = logging.getLogger(__name__)
from app.db.mongodb import MongoDBManager
from app.db.redis import RedisManager
from app.repositories.chat_history_repository import build_chat_history_repository
from app.repositories.consultation_repository import ConsultationRepository
from app.repositories.family_tree_repository import FamilyTreeRepository
from app.repositories.knowledge_report_repository import KnowledgeReportRepository
from app.repositories.medication_repository import (
    MedicationRepository,
    MedicationReminderRepository,
)
from app.repositories.prescription_draft_repository import PrescriptionDraftRepository
from app.repositories.user_profile_repository import UserProfileRepository
from app.services.agent.agent import Agent
from app.services.consultation.consultation_service import ConsultationService
from app.services.family.family_tree_service import FamilyTreeService
from app.services.medication.drug_catalog_service import DrugCatalogService
from app.services.medication.medication_service import MedicationService
from app.services.medication.medication_scheduler import start_medication_scheduler
from app.services.medication.prescription_ocr_service import PrescriptionOcrService
from app.services.medication.prescription_scan_service import PrescriptionScanService
from app.services.gemini import GeminiService
from app.services.guardrail import GuardrailService
from app.services.history.history_service import LineMessageHistoryService
from app.services.knowledge_reports.service import KnowledgeReportService
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
from app.services.line_messaging.rich_menu_service import RichMenuService
from app.services.line_messaging.token_manager import LineTokenManager
from app.services.medical.facility_name_index import configure_facility_names
from app.services.medical.medical_service import MedicalService, medical_service
from app.services.line_messaging.handler.facility_detail_handler import (
    LineFacilityDetailHandler,
)
from app.services.rag import (
    HybridRetriever,
    MongoAtlasTextRetriever,
    MongoAtlasVectorRetriever,
    RagAnswerService,
)
from app.services.rag.cohere_reranker import CohereReranker, VectorScoreReranker
from app.services.rag.firecrawl_client import FirecrawlClient
from app.services.rag.ingest_service import IngestService
from app.services.rag.user_document_answer_service import UserDocumentAnswerService
from app.services.rag.user_document_ingest_service import UserDocumentIngestService
from app.services.rag.user_document_retriever import UserDocumentVectorRetriever
from app.services.rag.query_rewriter import GeminiQueryRewriter
from app.services.rag.retrieval_grader import GeminiRetrievalGrader
from app.services.rag.web_search_service import WebSearchService
from app.services.users.user_profile_service import UserProfileService
from app.tools.knowledge_report_tools import configure_knowledge_report_tool
from app.tools.medical_tools import configure_medical_tools
from app.tools.official_site_tools import configure_official_site_tool
from app.tools.rag_tools import configure_rag_tool
from app.tools.user_document_tools import configure_user_document_tool
from app.tools.web_tools import configure_web_tool

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

_ingest_embeddings_kwargs: dict = {
    "model": settings.EMBEDDING_MODEL,
    "google_api_key": settings.GEMINI_API_KEY,
    "task_type": "RETRIEVAL_DOCUMENT",
}
if settings.MONGODB_VECTOR_DIM > 0:
    _ingest_embeddings_kwargs["output_dimensionality"] = settings.MONGODB_VECTOR_DIM
_ingest_embeddings = GoogleGenerativeAIEmbeddings(**_ingest_embeddings_kwargs)

_rag_vector_retriever = MongoAtlasVectorRetriever(
    embeddings=_query_embeddings,
    mongo_uri=settings.MONGODB_URI,
    db_name=settings.MONGODB_DB,
    collection_name=settings.MONGODB_COLLECTION,
    index_name=settings.MONGODB_VECTOR_INDEX,
    vector_field=settings.MONGODB_VECTOR_FIELD,
    text_field=settings.MONGODB_TEXT_FIELD,
    vector_dim=settings.MONGODB_VECTOR_DIM if settings.MONGODB_VECTOR_DIM > 0 else None,
    k=settings.RAG_RETRIEVE_CANDIDATES,
    min_score=settings.RAG_VECTOR_MIN_SCORE,
)

# Hybrid 與純向量共用同一個 ainvoke 介面，所以下游 RagAnswerService 不需要知道差別
if settings.RAG_HYBRID_ENABLED and settings.MONGODB_TEXT_INDEX:
    _rag_text_retriever = MongoAtlasTextRetriever(
        mongo_uri=settings.MONGODB_URI,
        db_name=settings.MONGODB_DB,
        collection_name=settings.MONGODB_COLLECTION,
        index_name=settings.MONGODB_TEXT_INDEX,
        text_field=settings.MONGODB_TEXT_FIELD,
        k=settings.RAG_RETRIEVE_CANDIDATES,
    )
    _rag_retriever = HybridRetriever(
        vector_retriever=_rag_vector_retriever,
        text_retriever=_rag_text_retriever,
        rrf_k=settings.RAG_RRF_K,
        limit=settings.RAG_RETRIEVE_CANDIDATES,
    )
    logger.info(
        "RAG hybrid retrieval enabled: vector=%s text=%s rrf_k=%s",
        settings.MONGODB_VECTOR_INDEX,
        settings.MONGODB_TEXT_INDEX,
        settings.RAG_RRF_K,
    )
else:
    _rag_retriever = _rag_vector_retriever
    if settings.RAG_HYBRID_ENABLED:
        logger.warning(
            "RAG_HYBRID_ENABLED=true but MONGODB_TEXT_INDEX unset; using vector-only"
        )

_firecrawl_client = None
if settings.FIRECRAWL_API_KEY:
    _firecrawl_client = FirecrawlClient(api_key=settings.FIRECRAWL_API_KEY)

if settings.COHERE_API_KEY:
    _rag_reranker = CohereReranker(
        api_key=settings.COHERE_API_KEY,
        model=settings.COHERE_RERANK_MODEL,
        timeout_seconds=settings.COHERE_RERANK_TIMEOUT_SECONDS,
    )
else:
    logger.warning(
        "COHERE_API_KEY unset; RAG will use vector-score top-n without Cohere"
    )
    _rag_reranker = VectorScoreReranker()

_rag_grader = None
_rag_rewriter = None
if settings.RAG_CRAG_ENABLED:
    _rag_grader = GeminiRetrievalGrader(gemini_service=_gemini_service)
    _rag_rewriter = GeminiQueryRewriter(gemini_service=_gemini_service)
else:
    logger.info("RAG_CRAG_ENABLED=false; skipping retrieval grader")

_ingest_service = None
if _firecrawl_client is not None and settings.MONGODB_URI and settings.MONGODB_COLLECTION:
    _ingest_service = IngestService(
        web_client=_firecrawl_client,
        embeddings=_ingest_embeddings,
        collection=MongoDBManager.get_database()[settings.MONGODB_COLLECTION],
        text_field=settings.MONGODB_TEXT_FIELD,
        vector_field=settings.MONGODB_VECTOR_FIELD,
        vector_dim=(
            settings.MONGODB_VECTOR_DIM if settings.MONGODB_VECTOR_DIM > 0 else None
        ),
    )

_knowledge_report_repository = KnowledgeReportRepository()
_knowledge_report_service = KnowledgeReportService(
    repository=_knowledge_report_repository,
    ingest_service=_ingest_service,
)
configure_knowledge_report_tool(_knowledge_report_service)

_web_search_service = WebSearchService(
    gemini_service=_gemini_service,
    web_client=_firecrawl_client,
    on_web_fallback_success=_knowledge_report_service.create_from_web_fallback,
)

_rag_answer_service = RagAnswerService(
    gemini_service=_gemini_service,
    retriever=_rag_retriever,
    reranker=_rag_reranker,
    rerank_top_n=settings.RAG_RERANK_TOP_N,
    max_chunks_per_article=settings.RAG_RERANK_MAX_CHUNKS_PER_ARTICLE,
    grader=_rag_grader,
    rewriter=_rag_rewriter,
    crag_enabled=settings.RAG_CRAG_ENABLED,
    web_search=_web_search_service,
    web_fallback_enabled=settings.RAG_WEB_FALLBACK_ENABLED,
)

_chat_history_repository = build_chat_history_repository()
_consultation_repository = ConsultationRepository()
configure_rag_tool(_rag_answer_service)
configure_web_tool(_web_search_service)
configure_medical_tools(medical_service)


async def preload_facility_name_index() -> None:
    """
    啟動時把全部院所名稱載入索引，供意圖判定分辨專名與泛稱。

    失敗不阻擋啟動：索引未載入時判定會退回「視為泛稱」，
    也就是退回未套類型過濾的現況行為，屬安全的降級方向。
    """
    try:
        names = await medical_service.repository.list_all_names()
    except Exception:
        logger.exception("[Startup] 載入院所名稱索引失敗，類型意圖判定將降級")
        return
    if not names:
        logger.warning("[Startup] 院所名稱索引為空，類型意圖判定將降級")
        return
    configure_facility_names(names)


configure_official_site_tool(
    liff_url=settings.LIFF_URL,
    public_base_url=settings.PUBLIC_BASE_URL,
)

_user_document_ingest_service: UserDocumentIngestService | None = None
_user_document_answer_service: UserDocumentAnswerService | None = None
if (
    settings.MONGODB_URI
    and settings.MONGODB_DB
    and settings.MONGODB_USER_DOCS_COLLECTION
):
    _user_document_ingest_service = UserDocumentIngestService(
        embeddings=_ingest_embeddings,
        collection=MongoDBManager.get_database()[settings.MONGODB_USER_DOCS_COLLECTION],
        text_field=settings.MONGODB_TEXT_FIELD,
        vector_field=settings.MONGODB_VECTOR_FIELD,
        vector_dim=(
            settings.MONGODB_VECTOR_DIM if settings.MONGODB_VECTOR_DIM > 0 else None
        ),
        ttl_seconds=settings.USER_DOCS_TTL_SECONDS,
    )

if (
    settings.MONGODB_URI
    and settings.MONGODB_DB
    and settings.MONGODB_USER_DOCS_COLLECTION
    and settings.MONGODB_USER_DOCS_VECTOR_INDEX
):
    _user_document_retriever = UserDocumentVectorRetriever(
        embeddings=_query_embeddings,
        mongo_uri=settings.MONGODB_URI,
        db_name=settings.MONGODB_DB,
        collection_name=settings.MONGODB_USER_DOCS_COLLECTION,
        index_name=settings.MONGODB_USER_DOCS_VECTOR_INDEX,
        vector_field=settings.MONGODB_VECTOR_FIELD,
        text_field=settings.MONGODB_TEXT_FIELD,
        vector_dim=settings.MONGODB_VECTOR_DIM if settings.MONGODB_VECTOR_DIM > 0 else None,
    )
    _user_document_answer_service = UserDocumentAnswerService(
        gemini_service=_gemini_service,
        retriever=_user_document_retriever,
    )

configure_user_document_tool(_user_document_answer_service)

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

_rich_menu_service = RichMenuService(
    get_access_token=_line_token_manager.get_token,
)

_user_profile_repository = UserProfileRepository()
_user_profile_service = UserProfileService(
    repo=_user_profile_repository, 
    rich_menu_service=_rich_menu_service
)

_consultation_service = ConsultationService(
    chat_history_repository=_chat_history_repository,
    repository=_consultation_repository,
    gemini_service=_gemini_service,
    user_profile_service=_user_profile_service,
)

_tts_service = TTSService()

_line_replier = LineReplier(
    token_manager=_line_token_manager,
    tts_service=_tts_service,
)
_facility_detail_handler = LineFacilityDetailHandler(
    medical_service=medical_service,
    replier=_line_replier,
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
    user_document_ingest_service=_user_document_ingest_service,
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

# 藥袋辨識。藥證庫在啟動時載入一次；load_from_path 內部已經處理檔案缺席
# 或損毀（記錄錯誤、回傳空清單），這裡不需要再包一層 try/except，否則等於
# 在「不讓應用啟動失敗」這個保證外面又加了一個會讓它啟動失敗的路徑。
_drug_catalog_service = DrugCatalogService.load_from_path(
    settings.DRUG_CATALOG_PATH, threshold=settings.DRUG_CATALOG_MATCH_THRESHOLD
)
_prescription_ocr_service = PrescriptionOcrService(
    gemini_service=_gemini_service,
    timeout_seconds=settings.PRESCRIPTION_SCAN_TIMEOUT_SECONDS,
)
# 各 repository 皆以 staticmethod 群組的形式存在（沿用本檔案其他組裝一貫的
# 慣例），直接把類別本身傳進去即可，不需要另外實例化。
_prescription_scan_service = PrescriptionScanService(
    ocr_service=_prescription_ocr_service,
    catalog_service=_drug_catalog_service,
    draft_repository=PrescriptionDraftRepository,
    medication_repository=MedicationRepository,
    reminder_repository=MedicationReminderRepository,
    family_tree_repository=FamilyTreeRepository,
    ttl_minutes=settings.PRESCRIPTION_DRAFT_TTL_MINUTES,
)

_line_event_handler = LineEventHandler(
    message_handler=_message_handler,
    media_handler=_media_handler,
    location_handler=_location_handler,
    facility_detail_handler=_facility_detail_handler,
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


def get_rich_menu_service() -> RichMenuService:
    return _rich_menu_service


def get_medical_service() -> MedicalService:
    return medical_service


def get_query_embeddings() -> GoogleGenerativeAIEmbeddings:
    """取得 RAG query embeddings 實例"""
    return _query_embeddings


def get_rag_retriever() -> MongoAtlasVectorRetriever | HybridRetriever:
    """取得 RAG retriever：依 RAG_HYBRID_ENABLED 為純向量或 hybrid（兩者介面相同）"""
    return _rag_retriever


def get_rag_answer_service() -> RagAnswerService:
    """取得 RAG 問答服務（知識庫檢索 + 生成）"""
    return _rag_answer_service


def get_user_profile_service() -> UserProfileService:
    return _user_profile_service


def get_tts_service() -> TTSService:
    return _tts_service


def get_family_tree_service() -> FamilyTreeService:
    return _family_tree_service


def get_medication_service() -> MedicationService:
    return _medication_service


def get_prescription_scan_service() -> PrescriptionScanService:
    return _prescription_scan_service


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


def get_knowledge_report_service() -> KnowledgeReportService:
    return _knowledge_report_service


def get_user_document_ingest_service() -> UserDocumentIngestService | None:
    """取得使用者上傳文件 ingest 服務；未設定 collection 時回傳 None。"""
    return _user_document_ingest_service


def get_user_document_answer_service() -> UserDocumentAnswerService | None:
    """取得使用者上傳文件問答服務；未設定 vector index 時回傳 None。"""
    return _user_document_answer_service


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


def require_prescription_scan_enabled() -> None:
    """功能開關關閉時，讓藥袋辨識相關端點表現得像不存在一樣，回 404。

    刻意在請求當下讀 `settings.PRESCRIPTION_SCAN_ENABLED`（而不是在模組載入時
    算好一個布林值存起來），測試才能單純以 app.dependency_overrides 覆寫這個
    dependency 來模擬「開啟」，不必真的改動 settings 或重新 import 這個模組。
    """
    if not settings.PRESCRIPTION_SCAN_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")


async def require_admin_user(
    current_user: CurrentUser = Depends(get_current_user),
    user_profile_service: UserProfileService = Depends(get_user_profile_service),
) -> CurrentUser:
    profile = await user_profile_service.get_user_profile(current_user.line_user_id)
    role = (profile or {}).get("role", "user")
    if role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user
