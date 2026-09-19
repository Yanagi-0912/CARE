from dataclasses import dataclass
import logging
import time

import jwt  # type: ignore[import-not-found]
from fastapi import Depends, Header, HTTPException
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.core.config import settings
from app.core.request_logging import log_stage

logger = logging.getLogger(__name__)
from app.db.mongodb import MongoDBManager
from app.db.redis import RedisManager
from app.repositories.chat_history_repository import build_chat_history_repository
from app.repositories.consultation_repository import ConsultationRepository
from app.repositories.conversation_log_repository import ConversationLogRepository
from app.repositories.family_delegation_repository import (
    FamilyDelegationRepository,
)
from app.repositories.family_rbac_metrics_repository import (
    FamilyRbacMetricsRepository,
)
from app.repositories.family_role_audit_repository import (
    FamilyRoleAuditRepository,
)
from app.repositories.family_tree_repository import FamilyTreeRepository
from app.repositories.health_alert_claim_repository import HealthAlertClaimRepository
from app.repositories.health_alert_threshold_repository import (
    HealthAlertThresholdRepository,
)
from app.repositories.health_measurement_repository import HealthMeasurementRepository
from app.repositories.menstrual_record_repository import MenstrualRecordRepository
from app.repositories.step_session_repository import StepSessionRepository
from app.repositories.knowledge_report_preview_repository import (
    KnowledgeReportPreviewRepository,
)
from app.repositories.knowledge_report_repository import KnowledgeReportRepository
from app.repositories.appointment_repository import AppointmentReminderRepository
from app.repositories.medication_repository import (
    MedicationLogRepository,
    MedicationRepository,
    MedicationReminderRepository,
)
from app.repositories.prescription_draft_repository import PrescriptionDraftRepository
from app.repositories.safety_alert_repository import SafetyAlertRepository
from app.repositories.user_profile_repository import UserProfileRepository
from app.services.agent.agent import Agent
from app.services.agent.utils.nodes import RAG_ROUTE_MODEL_PATH
from app.services.appointment.appointment_scheduler import (
    start_appointment_scheduler as _start_appointment_scheduler,
)
from app.services.appointment.appointment_service import AppointmentService
from app.services.consultation.consultation_service import ConsultationService
from app.services.family.family_authorization_service import (
    FamilyAuthorizationService,
)
from app.services.family.family_delegation_service import (
    FamilyDelegationService,
)
from app.services.family.family_role_service import FamilyRoleService
from app.services.family.family_tree_service import FamilyTreeService
from app.services.health.health_alert_service import HealthAlertService
from app.services.health.health_alert_threshold_service import (
    HealthAlertThresholdService,
)
from app.services.health.health_measurement_service import HealthMeasurementService
from app.services.health.menstrual_service import MenstrualRecordService
from app.services.health.step_service import StepService
from app.services.medication.drug_appearance_image_service import (
    resolve_drug_appearance_image_url,
)
from app.services.medication.drug_catalog_service import DrugCatalogService
from app.services.medication.drug_indication_service import DrugIndicationService
from app.services.medication.medication_service import MedicationService
from app.services.medication.medication_status_service import MedicationStatusService
from app.services.medication.medication_scheduler import start_medication_scheduler
from app.services.medication.prescription_ocr_service import PrescriptionOcrService
from app.services.medication.prescription_scan_service import PrescriptionScanService
from app.services.safety.drug_mention_extractor import DrugMentionExtractor
from app.services.safety.ingredient_overlap import (
    IngredientClass,
    IngredientWatchlist,
    load_local_action_forms,
)
from app.services.safety.emergency_alert_service import EmergencyFamilyAlertService
from app.services.lost.lost_classifier import LostIntentDetector
from app.services.lost.lost_location_service import LostLocationService
from app.services.medication.tcm_catalog_service import TcmCatalogService
from app.services.safety.otc_alert_service import OtcAlertService
from app.services.safety.atc_interaction import ClassPairTable
from app.services.safety.tcm_interaction import TcmInteractionTable
from app.services.safety.safety_alert_service import SafetyAlertService
from app.services.gemini import GeminiService
from app.services.guardrail import (
    CascadeGuardrailService,
    GuardrailService,
    LocalGuardrailClassifier,
)
from app.services.history.history_service import (
    RECENT_CONTEXT_MESSAGES,
    LineMessageHistoryService,
)
from app.services.knowledge_reports.preview_service import ContentPreviewService
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
from app.services.line_messaging.reply.remote_tts_service import RemoteTTSService
from app.services.line_messaging.reply.tts_service import TTSService, build_local_tts_service
from app.services.line_messaging.rich_menu_service import RichMenuService
from app.services.line_messaging.official_account import OfficialAccountService
from app.services.line_messaging.share_card import ShareCardService
from app.services.line_messaging.token_manager import LineTokenManager
from app.services.medical.facility_name_index import configure_facility_names
from app.services.medical.medical_service import MedicalService, medical_service
from app.services.medical.symptom_classification import (
    SymptomDepartmentService,
    SymptomNormalizer,
    UrgencyClassifier,
    load_symptom_table,
)
from app.services.medical.symptom_classification.urgency import URGENCY_MODEL_PATH
from app.services.medical.symptom_classification.vector_index import (
    DEFAULT_VECTOR_PATH,
    EMBEDDING_TASK_TYPE,
    VECTOR_DIM,
    SymptomVectorIndex,
    table_content_hash,
)
from app.services.line_messaging.handler.facility_detail_handler import (
    LineFacilityDetailHandler,
)
from app.services.rag import (
    HybridRetriever,
    MongoAtlasTextRetriever,
    MongoAtlasVectorRetriever,
    RagAnswerService,
)
from app.services.rag.claim_verification.identity import GeminiClaimIdentityVerifier
from app.services.rag.claim_verification.matcher import MongoAtlasClaimMatcher
from app.services.rag.claim_verification.normalizer import GeminiClaimNormalizer
from app.services.rag.claim_verification.service import ClaimVerificationService
from app.services.rag.cohere_reranker import CohereReranker, VectorScoreReranker
from app.services.rag.firecrawl_client import FirecrawlClient
from app.services.rag.ingest_service import IngestService
from app.services.rag.link_check import LinkChecker
from app.services.rag.whitelist import default_url_policy
from app.services.rag.user_document_answer_service import UserDocumentAnswerService
from app.services.rag.user_document_ingest_service import UserDocumentIngestService
from app.services.rag.user_document_retriever import UserDocumentVectorRetriever
from app.services.rag.query_rewriter import (
    REWRITE_THINKING_LEVEL,
    GeminiQueryRewriter,
)
from app.services.rag.retrieval_grader import (
    GRADE_THINKING_LEVEL,
    GeminiRetrievalGrader,
)
from app.services.rag.web_search_service import (
    WEB_GENERATE_THINKING_LEVEL,
    WebSearchService,
)
from app.services.medical_news.grader import GeminiNewsGrader
from app.services.medical_news.index_service import DrugNewsIndexService
from app.services.medical_news.kb_digest_service import KbDigestService
from app.services.medical_news.share_service import MedicalNewsShareService
from app.services.users.user_profile_service import UserProfileService
from app.services.media.tv_news_channels import TvNewsChannelMemory
from app.services.media.tv_news_lookup import TvNewsArticleFinder
from app.tools.claim_tools import configure_claim_tool
from app.tools.tv_news_tools import configure_tv_news_tool
from app.tools.knowledge_report_tools import configure_knowledge_report_tool
from app.tools.medication_status_tools import configure_medication_status_tool
from app.tools.medical_tools import configure_medical_tools
from app.tools.official_site_tools import configure_official_site_tool
from app.tools.rag_tools import configure_rag_tool
from app.tools.share_tools import configure_share_tool
from app.tools.symptom_tools import configure_symptom_tool
from app.tools.user_document_tools import configure_user_document_tool
from app.tools.web_tools import configure_web_tool

MongoDBManager.configure(settings.MONGODB_URI)
RedisManager.configure(settings.REDIS_URL)

_gemini_service = GeminiService(
    api_key=settings.GEMINI_API_KEY,
    model_name=settings.MODEL_NAME,
)

_llm_guardrail_service = GuardrailService(
    async_text_to_bool=_gemini_service.invoke_boolean_structured_output,
)

# 串接式 guardrail：本地分類器有把握時直接判，中間地帶才問 Gemini。
#
# 為什麼值得：guardrail 跑在每一則訊息的關鍵路徑上（graph 是
# START → guardrail → agent，沒有並行），線上實測 p50 2,036ms、最快也要
# 1,098ms。本地推論是次毫秒級，holdout 上 83.4% 的訊息不必再問 Gemini。
#
# 載入失敗就退回純 LLM 版本，不讓服務起不來——模型檔是建置期產出物
# （scripts/build_guardrail_model.py），它不在時的正確行為是「跟導入前
# 一模一樣」，而不是整個 RAG 守門失效。
try:
    _guardrail_service = CascadeGuardrailService(
        local=LocalGuardrailClassifier.load(),
        fallback=_llm_guardrail_service,
    )
    logger.info("Guardrail cascade enabled (local classifier + LLM fallback)")
except Exception:
    logger.exception("本地 guardrail 模型載入失敗，退回純 LLM 判斷")
    _guardrail_service = _llm_guardrail_service

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
        title_field=settings.MONGODB_TEXT_TITLE_FIELD,
        title_boost=settings.RAG_TEXT_TITLE_BOOST,
        k=settings.RAG_RETRIEVE_CANDIDATES,
    )
    _rag_retriever = HybridRetriever(
        vector_retriever=_rag_vector_retriever,
        text_retriever=_rag_text_retriever,
        rrf_k=settings.RAG_RRF_K,
        limit=settings.RAG_RETRIEVE_CANDIDATES,
        fusion_mode=settings.RAG_FUSION_MODE,
        alpha=settings.RAG_FUSION_ALPHA,
        leg_timeout_seconds=settings.RAG_RETRIEVE_LEG_TIMEOUT_SECONDS,
    )
    logger.info(
        "RAG hybrid retrieval enabled: vector=%s text=%s fusion=%s rrf_k=%s alpha=%s",
        settings.MONGODB_VECTOR_INDEX,
        settings.MONGODB_TEXT_INDEX,
        settings.RAG_FUSION_MODE,
        settings.RAG_RRF_K,
        settings.RAG_FUSION_ALPHA,
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
    # 分級也用獨立的低 thinking 實例（數字見 retrieval_grader.GRADE_THINKING_LEVEL）。
    # 不改共用的 _gemini_service：guardrail、問診等也在用它，沒一起量過。
    _rag_grader = GeminiRetrievalGrader(
        gemini_service=GeminiService(
            api_key=settings.GEMINI_API_KEY,
            model_name=settings.MODEL_NAME,
            thinking_level=GRADE_THINKING_LEVEL,
        )
    )
    # 改寫用獨立的低 thinking 實例：它與 CRAG 分級同時起跑，要比分級先跑完
    # 才不會讓使用者多等（數字見 query_rewriter.REWRITE_THINKING_LEVEL）。
    _rag_rewriter = GeminiQueryRewriter(
        gemini_service=GeminiService(
            api_key=settings.GEMINI_API_KEY,
            model_name=settings.MODEL_NAME,
            thinking_level=REWRITE_THINKING_LEVEL,
        )
    )
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
        url_policy=default_url_policy(),
    )

_knowledge_report_repository = KnowledgeReportRepository()
_knowledge_report_preview_repository = KnowledgeReportPreviewRepository()
# 沒有 Firecrawl 就沒有預覽可抓；服務為 None 時預覽端點回 503，而 approve 的
# 快照綁定驗證仍然生效（沒有預覽就核准不了），不會退回舊的重抓路徑。
_content_preview_service = None
if _firecrawl_client is not None:
    _content_preview_service = ContentPreviewService(
        repository=_knowledge_report_preview_repository,
        web_client=_firecrawl_client,
        ttl_minutes=settings.KNOWLEDGE_PREVIEW_TTL_MINUTES,
        max_urls=settings.KNOWLEDGE_PREVIEW_MAX_URLS,
        return_max_chars=settings.KNOWLEDGE_PREVIEW_RETURN_MAX_CHARS,
        url_policy=default_url_policy(),
    )

_knowledge_report_service = KnowledgeReportService(
    repository=_knowledge_report_repository,
    ingest_service=_ingest_service,
    url_policy=default_url_policy(),
    preview_service=_content_preview_service,
    # 網搜降級自動建報前，先用與 agent 相同的 guardrail 判斷問題是否與健康
    # 醫療相關（理由見 KnowledgeReportService._is_health_related）。刻意接
    # cascade 而不是只接本地分類器：「法國國歌」「軍艦進行曲」這類短問句本地
    # 模型給 p≈0.48、落在升級區，真正判出「不相關」的是 LLM 那一層。
    topic_guard=_guardrail_service.allow_rag_tool,
)
configure_knowledge_report_tool(_knowledge_report_service)

# 兩條回答路徑共用同一個 checker，快取才是共用的：知識庫路徑查過的網址，
# 網搜路徑（以及下一輪對話）能直接命中，不必再打一次 HEAD。
_link_checker = None
if settings.RAG_LINK_CHECK_ENABLED:
    _link_checker = LinkChecker(
        timeout_seconds=settings.RAG_LINK_CHECK_TIMEOUT_SECONDS,
        ok_ttl_seconds=settings.RAG_LINK_CHECK_OK_TTL_SECONDS,
        dead_ttl_seconds=settings.RAG_LINK_CHECK_DEAD_TTL_SECONDS,
    )
else:
    logger.info("RAG_LINK_CHECK_ENABLED=false; citation URLs will not be verified")

_web_search_service = WebSearchService(
    # 網搜答案生成用獨立的低 thinking 實例（數字見
    # web_search_service.WEB_GENERATE_THINKING_LEVEL）。不改共用的
    # _gemini_service：知識庫生成、guardrail、問診都在用它，沒一起量過。
    gemini_service=GeminiService(
        api_key=settings.GEMINI_API_KEY,
        model_name=settings.MODEL_NAME,
        thinking_level=WEB_GENERATE_THINKING_LEVEL,
    ),
    web_client=_firecrawl_client,
    on_web_fallback_success=_knowledge_report_service.create_from_web_fallback,
    link_checker=_link_checker,
    en_search_domains=settings.RAG_WEB_SEARCH_EN_DOMAINS.split(","),
)

_rag_answer_service = RagAnswerService(
    gemini_service=_gemini_service,
    retriever=_rag_retriever,
    reranker=_rag_reranker,
    rerank_top_n=settings.RAG_RERANK_TOP_N,
    max_chunks_per_article=settings.RAG_RERANK_MAX_CHUNKS_PER_ARTICLE,
    grader=_rag_grader,
    rewriter=_rag_rewriter,
    speculative_generate=settings.RAG_SPECULATIVE_GENERATE,
    crag_enabled=settings.RAG_CRAG_ENABLED,
    web_search=_web_search_service,
    web_fallback_enabled=settings.RAG_WEB_FALLBACK_ENABLED,
    degraded_min_score=settings.RAG_DEGRADED_MIN_SCORE,
    link_checker=_link_checker,
    total_timeout_seconds=settings.RAG_ANSWER_TIMEOUT_SECONDS,
)

_chat_history_repository = build_chat_history_repository(
    max_messages=RECENT_CONTEXT_MESSAGES
)
_conversation_log_repository = ConversationLogRepository()
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

# 查核判定卡。matcher 沿用既有的 embedding 索引、向量欄位與 query
# embeddings，不另建 claim 專用索引（claim-verdict-card/design.md 決策
# 2）；未啟用時不建立服務也不 configure tool（registry.py 的 verify_claim
# 靠這個決定要不要出現在工具清單，見 app/tools/claim_tools.py 的
# is_claim_tool_configured）。
#
# vector_field 明確傳入 settings.MONGODB_VECTOR_FIELD——不能省略：matcher
# 建構子的 vector_field 預設值雖然也是 "embedding"，但省略等於讓正確與否
# 繫於「兩處硬寫的常數剛好相同」這個巧合，且會讓這裡看起來像是忘記接線，
# 而非刻意沿用既有欄位。2026-08-18 對 production Atlas 實測證實：這個
# 參數當初漏接（連預設值都指向錯的欄位）會讓 $vectorSearch 對純文字的
# claim 欄位查詢，MongoDB 回 OperationFailure，又被 matcher 的 fail-open
# 設計吞掉，導致 verify_claim 線上每次都靜默回「證據不足」且不報錯。
#
# content_field 明確傳入 settings.MONGODB_TEXT_FIELD——同樣不能省略，理由
# 與上面 vector_field 完全相同：matcher 建構子的 content_field 預設值是
# 硬寫的 "chunk_content"，而 config.py 的 MONGODB_TEXT_FIELD 預設值是
# "text"、.env.example 也寫 "text"。省略等於讓「查得到的判定卡有沒有理由
# 依據」繫於「.env 裡的值剛好等於這個硬寫常數」這個巧合——目前不炸純粹
# 因為當下這份 .env 剛好設成 chunk_content。一旦照 .env.example 部署，
# match.content 會是空字串，_rewrite_reasoning 的 prompt 變成「查核報告
# 內容：」後面空白，Gemini 只看得到使用者問句，會憑空編出一段「查核報告
# 怎麼看待這則說法」，貼在標著「判定來源：台灣事實查核中心」的卡片上——
# 核心約束（判定與理由都要有實際查核依據）被實質破壞（claim-verdict-card
# 最終 review C2 finding）。matcher.py 另外對空 content 做了執行期防線
# （視為未命中），這裡的明確傳入是避免一開始就走到那條防線。
#
# GeminiClaimNormalizer 與 ClaimVerificationService 都刻意明確傳入
# gemini_service：兩者的 fail-open 設計會把「忘記注入」靜默降級成
# 「永遠不正規化」／「永遠用降級理由」而非報錯，漏寫在這裡不會被任何測試
# 攔下來（Task 3 review 記錄的已知風險）。
#
# identity_verifier 的風險方向不同、但一樣真實：ClaimVerificationService
# 把它設計成可選參數（None 時直接跳過同一性驗證），是為了不動既有測試、
# 向後相容——代價是如果這裡漏寫 identity_verifier=...，不會有任何
# TypeError 或例外，效果是同一性驗證整條防線悄悄消失，向量誤配
# （design.md 決策 9 量到的 65%）原樣回到線上。GeminiClaimIdentityVerifier
# 本身在兩個依賴都沒給時會 raise（見 identity.py 模組 docstring），但那只
# 防得住「verifier 建構出來、卻沒接 gemini_service」，防不住「這裡整段
# 忘記傳 identity_verifier 參數」——兩種疏漏由兩道不同防線各自擋，這裡
# 必須兩個都接對；tests/unit/test_dependencies.py 另外釘住這裡的接線。
_claim_verification_service: ClaimVerificationService | None = None
if settings.CLAIM_VERIFICATION_ENABLED:
    _claim_matcher = MongoAtlasClaimMatcher(
        embeddings=_query_embeddings,
        mongo_uri=settings.MONGODB_URI,
        db_name=settings.MONGODB_DB,
        collection_name=settings.MONGODB_COLLECTION,
        index_name=settings.MONGODB_VECTOR_INDEX,
        vector_field=settings.MONGODB_VECTOR_FIELD,
        content_field=settings.MONGODB_TEXT_FIELD,
        min_score=settings.CLAIM_MATCH_MIN_SCORE,
    )
    _claim_identity_verifier = GeminiClaimIdentityVerifier(
        gemini_service=_gemini_service
    )
    _claim_verification_service = ClaimVerificationService(
        normalizer=GeminiClaimNormalizer(gemini_service=_gemini_service),
        matcher=_claim_matcher,
        gemini_service=_gemini_service,
        related_retriever=_rag_retriever,
        identity_verifier=_claim_identity_verifier,
    )
    configure_claim_tool(_claim_verification_service)
    # 電視新聞畫面的查核多附一顆「看新聞原文」。找新聞靠 Firecrawl 搜尋；沒有
    # 金鑰時仍然提供工具，只是不附連結——判定卡本身不依賴它。
    configure_tv_news_tool(
        TvNewsArticleFinder(_firecrawl_client.search if _firecrawl_client else None),
        TvNewsChannelMemory(),
    )
else:
    logger.info("CLAIM_VERIFICATION_ENABLED=false; verify_claim tool not configured")

# 表壞掉時刻意不降級：帶著解析不出科別的對照表提供服務，會產生「系統說查過了
# 但附近沒有」的回覆，比功能不存在更難察覺（見 symptom_table 模組註解）。
_symptom_table = load_symptom_table()

# 症狀比對的向量索引。task_type 與維度刻意不沿用 RAG 那組（見 design 決策 12）：
# RAG 是「問句 → 文件段落」的非對稱檢索，症狀比對是短語對短語的對稱相似度。
_symptom_embeddings = GoogleGenerativeAIEmbeddings(
    model=settings.EMBEDDING_MODEL,
    google_api_key=settings.GEMINI_API_KEY,
    task_type=EMBEDDING_TASK_TYPE,
    output_dimensionality=VECTOR_DIM,
)

# 向量檔缺席或與表不同步時回 None，比對層自動退回 LLM 全表兜底——降級而非中斷。
# 表改過就要重跑 scripts/build_symptom_vectors.py。
_symptom_vector_index = SymptomVectorIndex.load(
    DEFAULT_VECTOR_PATH,
    expected_hash=table_content_hash(_symptom_table.terms),
    # 與上面 _symptom_embeddings 查詢用的是同一個設定值；向量檔以別的模型建立時拒用。
    expected_model=settings.EMBEDDING_MODEL,
)

_symptom_department_service = SymptomDepartmentService(
    table=_symptom_table,
    normalizer=SymptomNormalizer(
        table_terms=_symptom_table.terms,
        vector_index=_symptom_vector_index,
        embed_query=_symptom_embeddings.aembed_query,
        gemini_service=_gemini_service,
    ),
)
configure_symptom_tool(_symptom_department_service)

# 急迫度判斷。刻意與科別建議分開建構：它擋在整個 agent 之前，不屬於任何工具，
# 也不依賴對照表——對照表壞掉時科別建議可以不上線，安全檢查不行。
#
# 本地模型先判、沒把握才問 Gemini（見 urgency.py 模組註解）。模型檔不在或壞掉時
# 退回純 LLM 判斷——與導入前一模一樣，而不是整個安全檢查失效。
try:
    _urgency_local = LocalGuardrailClassifier.load(URGENCY_MODEL_PATH)
    logger.info("Urgency cascade enabled (local classifier + LLM fallback)")
except Exception:
    logger.exception("本地急迫度模型載入失敗，退回純 LLM 判斷")
    _urgency_local = None
_urgency_classifier = UrgencyClassifier(
    gemini_service=_gemini_service, local=_urgency_local
)

# 本地「直接送 RAG」分類器：有把握時跳過 agent 選工具的那次呼叫（見
# AgentNodes._local_rag_shortcut）。模型檔不在或壞掉時每一則都照舊問 agent，
# 不擋啟動。
try:
    _rag_router = LocalGuardrailClassifier.load(RAG_ROUTE_MODEL_PATH)
    logger.info("RAG route shortcut enabled (local classifier)")
except Exception:
    logger.exception("本地 RAG 分流模型載入失敗，每一則都交給 agent 決定")
    _rag_router = None

_care_agent = Agent(
    llm=_gemini_service.chat_model,
    guardrail_service=_guardrail_service,
    urgency_classifier=_urgency_classifier,
    rag_router=_rag_router,
)

_line_history_service = LineMessageHistoryService(
    _chat_history_repository, conversation_log=_conversation_log_repository
)

_line_token_manager = LineTokenManager(
    channel_id=settings.LINE_CHANNEL_ID,
    channel_secret=settings.LINE_CHANNEL_SECRET,
)

_line_loading_animation_service = LineLoadingAnimationService(_line_token_manager)

# 分享卡：關鍵字秒回（message handler）與 AI 工具 share_care 共用同一個服務。
_official_account_service = OfficialAccountService(_line_token_manager)
_share_card_service = ShareCardService(
    _official_account_service, liff_url=settings.LIFF_URL
)
configure_share_tool(_share_card_service)

_rich_menu_service = RichMenuService(
    get_access_token=_line_token_manager.get_token,
)

_user_profile_repository = UserProfileRepository()
_user_profile_service = UserProfileService(
    repo=_user_profile_repository, 
    rich_menu_service=_rich_menu_service
)


def _build_tts_service(service_url: str) -> TTSService | RemoteTTSService:
    """有 care-tts（TTS_SERVICE_URL）就交給它合成、存檔；沒有就在本行程合成（本機開發）。

    音檔不能留在 backend 容器裡：部署換 pod 就全丟，backend 開多份時也會被分到沒有
    那個檔的 pod。詳見 remote_tts_service。
    """
    if service_url.strip():
        return RemoteTTSService(service_url)
    return build_local_tts_service()


_tts_service = _build_tts_service(settings.TTS_SERVICE_URL)

_line_replier = LineReplier(
    token_manager=_line_token_manager,
    tts_service=_tts_service,
)
_facility_detail_handler = LineFacilityDetailHandler(
    medical_service=medical_service,
    replier=_line_replier,
)

# 藥證庫在啟動時載入一次；load_from_path 內部已經處理檔案缺席或損毀
# （記錄錯誤、回傳空清單），這裡不需要再包一層 try/except，否則等於在
# 「不讓應用啟動失敗」這個保證外面又加了一個會讓它啟動失敗的路徑。
# 藥袋辨識與用藥風險偵測共用這一份，兩邊都不重新載入。
_drug_catalog_service = DrugCatalogService.load_from_path(
    settings.DRUG_CATALOG_PATH, threshold=settings.DRUG_CATALOG_MATCH_THRESHOLD
)

# 仿單適應症同樣在啟動時載入一次，load_from_path 內部已處理缺席與損毀
# （記錄錯誤、回傳空服務），這裡不再包一層。藥袋辨識（比對記錄）與用藥清單
# （呈現）共用這一份，兩邊都不重新載入。
_drug_indication_service = DrugIndicationService.load_from_path(
    settings.DRUG_INDICATION_PATH
)

# 用藥風險偵測。組裝本身沒有任何 I/O，因此無條件建好；真正的閘門在下面
# handler 的注入——SAFETY_ALERT_ENABLED 為 false 時 handler 拿到 None，
# 整條路徑（抽取、判定、推播）一步都不會執行。
_drug_mention_extractor = DrugMentionExtractor(
    gemini_service=_gemini_service,
    timeout_seconds=settings.SAFETY_ALERT_TIMEOUT_SECONDS,
)
# 家庭授權的唯一決策點。repository 皆以 staticmethod 群組的形式存在（沿用本
# 檔案其他組裝一貫的慣例），直接把類別本身傳進去即可。
_family_authorization_service = FamilyAuthorizationService(
    family_tree_repository=FamilyTreeRepository,
    delegation_repository=FamilyDelegationRepository,
    enforcement_enabled=settings.FAMILY_RBAC_ENFORCED,
    # 遷移指標的計數器。判準 1（收緊差異比例）與判準 4（受影響擁有者清單）
    # 的原始資料來源；寫入失敗一律吞掉，不影響授權。
    metrics_repository=FamilyRbacMetricsRepository,
)

# 查服藥狀況（LINE 裡問「我今天要吃什麼藥」「媽媽吃藥了沒」）。查家人時經過同一個
# 授權決策點；repository 同樣直接傳類別本身。
_medication_status_service = MedicationStatusService(
    family_tree_repository=FamilyTreeRepository,
    authorization_service=_family_authorization_service,
    reminder_repository=MedicationReminderRepository,
    medication_repository=MedicationRepository,
    log_repository=MedicationLogRepository,
)
configure_medication_status_tool(_medication_status_service)

_safety_alert_service = SafetyAlertService(
    extractor=_drug_mention_extractor,
    catalog_service=_drug_catalog_service,
    alert_repository=SafetyAlertRepository,
    family_tree_repository=FamilyTreeRepository,
    replier=_line_replier,
    user_profile_service=_user_profile_service,
    dedupe_hours=settings.SAFETY_ALERT_DEDUPE_HOURS,
    # 通知政策的判定點。高風險通報是唯一繞過 LIFF 授權邊界把健康資訊送出去的
    # 通道，因此它也要經過同一個決策點——只是走的是 NOTIFICATION_POLICY 這張
    # 表，不是 PERMISSIONS。
    authorization_service=_family_authorization_service,
)
_enabled_safety_alert_service = (
    _safety_alert_service if settings.SAFETY_ALERT_ENABLED else None
)

# 非處方藥成分重複偵測。白名單與局部作用劑型清單在啟動時各讀一次檔——它們是
# 靜態設定，每次偵測重讀只是白花 I/O；讀不到時 IngredientWatchlist 回空清單，
# 效果是「不偵測任何重複」，與整條路徑對主流程 fail-open 的方向一致。
_otc_watchlist = IngredientWatchlist.load_from_path()
# 抗膽鹼疊加清單。同樣是靜態設定，讀不到時回空清單＝不偵測疊加。
_anticholinergics = IngredientClass.load_from_path()
# 中藥庫與中西藥配對表。同樣是建置期產出的靜態檔，執行期不對外連線；
# 讀不到時兩者都退化成「不辨識中藥／不偵測中西藥交互作用」。
_class_pairs = ClassPairTable.load_from_path()
_tcm_watch_herbs = IngredientWatchlist(
    entry.get("name", "")
    for entry in (
        IngredientWatchlist._load_payload("resources/tcm_watch_herbs.json").get("herbs")
        or []
    )
)
_tcm_catalog_service = TcmCatalogService.load_from_path()
_tcm_interactions = TcmInteractionTable.load_from_path()
_otc_local_action_forms = load_local_action_forms()
_otc_alert_service = OtcAlertService(
    catalog_service=_drug_catalog_service,
    medication_repository=MedicationRepository,
    reminder_repository=MedicationReminderRepository,
    replier=_line_replier,
    watchlist=_otc_watchlist,
    anticholinergics=_anticholinergics,
    class_pairs=_class_pairs,
    tcm_watch_herbs=_tcm_watch_herbs,
    tcm_catalog_service=_tcm_catalog_service,
    tcm_interactions=_tcm_interactions,
    local_action_forms=_otc_local_action_forms,
    # 與高風險通報走同一個決策點，只是查 NOTIFICATION_POLICY 裡的另一個種類
    # （otc_medication_added）。收到通知 SHALL NOT 改變收件人的資料存取權。
    authorization_service=_family_authorization_service,
    user_profile_service=_user_profile_service,
)
_enabled_otc_alert_service = (
    _otc_alert_service if settings.OTC_ALERT_ENABLED else None
)

# 對話中判定為緊急時通報家人。與 OTC 通報走同一個決策點（家庭授權服務），
# 只是查 NOTIFICATION_POLICY 裡的 emergency_detected。刻意沒有開關：
# 這是安全通報，不是可選功能——沒有合格收件人時它自己就不會送出。
_emergency_family_alert_service = EmergencyFamilyAlertService(
    replier=_line_replier,
    authorization_service=_family_authorization_service,
    user_profile_service=_user_profile_service,
)
# 走失求救與即時位置分享。收件人同樣走 NOTIFICATION_POLICY（elder_lost）；沒有
# 開關，理由同緊急通報。LIFF_ID 沒設時卡片不放定位頁按鈕，只剩「傳送一次位置」。
_lost_location_service = LostLocationService(
    replier=_line_replier,
    authorization_service=_family_authorization_service,
    user_profile_service=_user_profile_service,
    liff_id=settings.LIFF_ID,
    # 關鍵字先判，認不得的講法與外語交給本地分類器；模型檔缺席時只用關鍵字。
    intent_detector=LostIntentDetector.load(),
)

_message_handler = LineMessageHandler(
    agent=_care_agent,
    history_service=_line_history_service,
    user_profile_service=_user_profile_service,
    replier=_line_replier,
    loading_animation_service=_line_loading_animation_service,
    safety_alert_service=_enabled_safety_alert_service,
    emergency_family_alert_service=_emergency_family_alert_service,
    share_card_service=_share_card_service,
    lost_location_service=_lost_location_service,
    urgency_classifier=_urgency_classifier,
)
_media_handler = LineMediaHandler(
    agent=_care_agent,
    history_service=_line_history_service,
    user_profile_service=_user_profile_service,
    replier=_line_replier,
    loading_animation_service=_line_loading_animation_service,
    user_document_ingest_service=_user_document_ingest_service,
    safety_alert_service=_enabled_safety_alert_service,
    emergency_family_alert_service=_emergency_family_alert_service,
    lost_location_service=_lost_location_service,
    urgency_classifier=_urgency_classifier,
)
_location_handler = LineLocationHandler(
    agent=_care_agent,
    history_service=_line_history_service,
    user_profile_service=_user_profile_service,
    replier=_line_replier,
    loading_animation_service=_line_loading_animation_service,
    lost_location_service=_lost_location_service,
)
# 稽核與角色指派共用同一份：移除成員收回的是全部權限，要跟角色變更排在同一條時序上。
_family_tree_service = FamilyTreeService(audit_repository=FamilyRoleAuditRepository)
_family_role_service = FamilyRoleService(
    authorization_service=_family_authorization_service,
    family_tree_repository=FamilyTreeRepository,
    audit_repository=FamilyRoleAuditRepository,
)
_family_delegation_service = FamilyDelegationService(
    delegation_repository=FamilyDelegationRepository,
    family_tree_repository=FamilyTreeRepository,
    audit_repository=FamilyRoleAuditRepository,
    activation_enabled=settings.FAMILY_DELEGATION_ACTIVATION_ENABLED,
)
_medication_service = MedicationService(indication_service=_drug_indication_service)

# 個人健康紀錄（personal-health-tracking）。Task 3 組裝提醒範圍；Task 4 接著
# 加血壓血糖量測；Task 5 在這裡接著加經期；Task 6 加計步；Task 7 加超出
# 範圍與經期異常推播。
_health_alert_threshold_service = HealthAlertThresholdService(
    repository=HealthAlertThresholdRepository
)
# 超出範圍／經期異常推播。與高風險通報、非處方藥通知、緊急通報走同一個
# LineReplier；收件人判定走同一個 _family_authorization_service，只是查
# NOTIFICATION_POLICY 裡的 health_out_of_range（見該服務模組 docstring）。
# HEALTH_ALERTS_ENABLED 預設 false：憑證與文案就緒前，等級照常判定與儲存，
# 只是不推播。
_health_alert_service = HealthAlertService(
    replier=_line_replier,
    claim_repository=HealthAlertClaimRepository,
    authorization_service=_family_authorization_service,
    user_profile_service=_user_profile_service,
    enabled=settings.HEALTH_ALERTS_ENABLED,
    liff_url=settings.LIFF_URL,
)
_health_measurement_service = HealthMeasurementService(
    measurement_repository=HealthMeasurementRepository,
    threshold_repository=HealthAlertThresholdRepository,
    alert_service=_health_alert_service,
)
# 經期是 PERSONAL 分類（見 app/models/family_authorization.py），建立時要看
# 本人個人健康檔案的性別，因此注入既有的 _user_profile_service（在上面已
# 組裝好），不另外重建一份。
_menstrual_record_service = MenstrualRecordService(
    repository=MenstrualRecordRepository,
    user_profile_service=_user_profile_service,
    alert_service=_health_alert_service,
)
# 計步：repository 直接傳類別本身（同其餘 health 服務的慣例），clock 使用
# StepService 自己的預設值（真正的 UTC now），不需要在這裡另外指定。
_step_service = StepService(repository=StepSessionRepository)

# 掛號提醒。出發／到診的授權在服務層（LIFF 與 LINE postback 兩個入口共用），
# 所以授權服務注入給服務本身；CRUD 的授權仍在 router，與用藥相同。
_appointment_repository = AppointmentReminderRepository()
_appointment_service = AppointmentService(
    repository=_appointment_repository,
    authorization_service=_family_authorization_service,
    user_profile_service=_user_profile_service,
)

_consultation_service = ConsultationService(
    chat_history_repository=_conversation_log_repository,
    repository=_consultation_repository,
    gemini_service=_gemini_service,
    user_profile_service=_user_profile_service,
    medication_service=_medication_service,
    appointment_repository=_appointment_repository,
)

# 藥袋辨識。藥證庫沿用上面已經載入的那一份（見 _drug_catalog_service）。
_prescription_ocr_service = PrescriptionOcrService(
    gemini_service=_gemini_service,
    timeout_seconds=settings.PRESCRIPTION_SCAN_TIMEOUT_SECONDS,
)
# 各 repository 皆以 staticmethod 群組的形式存在（沿用本檔案其他組裝一貫的
# 慣例），直接把類別本身傳進去即可，不需要另外實例化。
_prescription_scan_service = PrescriptionScanService(
    authorization_service=_family_authorization_service,
    ocr_service=_prescription_ocr_service,
    catalog_service=_drug_catalog_service,
    draft_repository=PrescriptionDraftRepository,
    medication_repository=MedicationRepository,
    reminder_repository=MedicationReminderRepository,
    family_tree_repository=FamilyTreeRepository,
    # 其餘參數（image_dir／public_base_url／url_path）皆有預設值，讀
    # app.core.config.settings，正式組裝時不需要額外帶入。
    appearance_image_resolver=resolve_drug_appearance_image_url,
    ttl_minutes=settings.PRESCRIPTION_DRAFT_TTL_MINUTES,
    indication_service=_drug_indication_service,
    otc_alert_service=_enabled_otc_alert_service,
)

# ── 每日醫療消息卡（medical-news-push）────────────────────────────────
#
# 索引服務需要 Firecrawl；沒有 API key 就沒有搜尋能力，此時服務為 None、
# 索引排程不啟動，推播仍可照常供應 Tier 2（那條路只讀既有知識庫）。這個
# 降級方向是刻意的：Tier 1 缺席時使用者仍每天收得到東西。
_drug_news_index_service = None
if _firecrawl_client is not None:
    _drug_news_index_service = DrugNewsIndexService(
        web_client=_firecrawl_client,
        grader=GeminiNewsGrader(gemini_service=_gemini_service),
        max_age_days=settings.MEDICAL_NEWS_MAX_AGE_DAYS,
        search_limit=settings.MEDICAL_NEWS_SEARCH_LIMIT,
    )

# Tier 2 讀 CARE-data 每日 ETL 維護的兩個 collection：官方與 TFC 的知識庫（與 RAG 共用），
# 以及健康媒體（daily_health_news，只給推播用）。不新增外部依賴。

_kb_digest_service = None
if settings.MONGODB_URI and settings.MONGODB_COLLECTION:
    _kb_digest_service = KbDigestService(
        collection=MongoDBManager.get_database()[settings.MONGODB_COLLECTION],
        max_age_days=settings.MEDICAL_NEWS_MAX_AGE_DAYS,
        # 健康媒體，官方當天沒有新內容時補位（kb_digest_service.recent_articles）。
        # 沒有開關：collection 空的時候媒體那一群就是空的，行為與加入之前相同。
        media_collection=MongoDBManager.get_daily_health_news_collection(),
    )

_medical_news_share_service = MedicalNewsShareService(
    replier=_line_replier,
    family_tree_service=_family_tree_service,
    user_profile_service=_user_profile_service,
    daily_share_limit=settings.MEDICAL_NEWS_DAILY_SHARE_LIMIT,
)

# 加好友歡迎卡與 LIFF 首次登入共用：兩者都要替還沒有 profile 的人決定語言。
_line_language_service = LineLanguageService(
    get_access_token=_line_token_manager.get_token,
)

_line_event_handler = LineEventHandler(
    message_handler=_message_handler,
    media_handler=_media_handler,
    location_handler=_location_handler,
    facility_detail_handler=_facility_detail_handler,
    replier=_line_replier,
    medication_service=_medication_service,
    medical_news_share_service=_medical_news_share_service,
    appointment_service=_appointment_service,
    line_language_service=_line_language_service,
    liff_url=settings.LIFF_URL,
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


def get_conversation_log_repository() -> ConversationLogRepository:
    return _conversation_log_repository


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


async def warm_rag_connections() -> None:
    """啟動時把 RAG 檢索用的 Mongo 連線先建立起來。

    為什麼需要：retriever 的 client 是首次 `ainvoke` 才懶建的，不在既有的
    startup 路徑上（lifespan 的 `ensure_indexes` 走的是 `MongoDBManager`
    那條）。所以每次部署後**第一個問問題的使用者**要獨自承擔建立連線的
    成本——健康網路下約 0.7-0.9 秒，網路不佳時更久。這裡先把那一次付掉。

    收益不大（秒級、每次啟動一次），但成本也接近零：背景執行、失敗不影響
    服務。

    失敗只記錄不拋：連不上 Mongo 時該讓服務照常啟動、由既有的錯誤路徑
    處理，而不是讓整個 app 起不來——暖機是最佳化，不是前置條件。
    """
    warmup = getattr(_rag_retriever, "warmup", None)
    if warmup is None:
        return
    t0 = time.perf_counter()
    try:
        await warmup()
    except Exception:
        logger.exception("rag_warmup_failed; first query will pay connection cost")
        return
    log_stage(logger, "rag_warmup", ms=int((time.perf_counter() - t0) * 1000))


def get_rag_answer_service() -> RagAnswerService:
    """取得 RAG 問答服務（知識庫檢索 + 生成）"""
    return _rag_answer_service


def get_claim_verification_service() -> ClaimVerificationService | None:
    """取得查核判定卡服務；`CLAIM_VERIFICATION_ENABLED=false` 時回傳 `None`。

    回傳型別刻意是 `Optional`，不是像 `get_rag_answer_service()` 那樣直接
    回傳非 None 的服務——`ClaimVerificationService` 是本專案唯一一個「整組
    可能整個不存在」的服務（見上方 `_claim_verification_service` 建構那段
    註解），呼叫端（例如 `scripts/rag_eval.py` 的 `--with-verdict`）本來就
    必須自己判斷「有沒有配置」，讓型別誠實反映這件事，比回傳一個假的
    服務物件或拋例外更不會被誤用。
    """
    return _claim_verification_service


def get_user_profile_service() -> UserProfileService:
    return _user_profile_service


def get_tts_service() -> TTSService | RemoteTTSService:
    return _tts_service


def get_family_tree_service() -> FamilyTreeService:
    return _family_tree_service


def get_family_role_service() -> FamilyRoleService:
    """家庭角色指派。提權防護的六道檢查都在這支服務裡。"""
    return _family_role_service


def get_family_delegation_service() -> FamilyDelegationService:
    """委任授權。建立的路徑在核可流程確定之前不對終端使用者開放。"""
    return _family_delegation_service


def get_family_authorization_service() -> FamilyAuthorizationService:
    """家庭授權的唯一決策點。

    跨使用者的端點一律經由這裡判定，SHALL NOT 自行判斷「他是不是家人」或
    「他是什麼角色」。"""
    return _family_authorization_service


def get_medication_service() -> MedicationService:
    return _medication_service


def get_health_alert_threshold_service() -> HealthAlertThresholdService:
    return _health_alert_threshold_service


def get_health_measurement_service() -> HealthMeasurementService:
    return _health_measurement_service


def get_menstrual_record_service() -> MenstrualRecordService:
    return _menstrual_record_service


def get_step_service() -> StepService:
    return _step_service


def get_appointment_service() -> AppointmentService:
    return _appointment_service


def get_appointment_repository() -> AppointmentReminderRepository:
    return _appointment_repository


def start_appointment_scheduler(*, enabled: bool = True):
    """掛號提醒排程器。家屬名單走家庭授權服務的 `notification_recipients`，
    與高風險藥物、OTC、緊急通報是同一個 resolver。"""
    return _start_appointment_scheduler(
        enabled=enabled,
        replier=_line_replier,
        repository=_appointment_repository,
        authorization_service=_family_authorization_service,
        user_profile_service=_user_profile_service,
    )


def get_drug_news_index_service():
    """索引服務。沒有 Firecrawl 時為 None，呼叫端據此不啟動索引排程。"""
    return _drug_news_index_service


def get_kb_digest_service():
    """Tier 2 選材。沒有知識庫連線時為 None。"""
    return _kb_digest_service


def get_medical_news_share_service() -> MedicalNewsShareService:
    return _medical_news_share_service


def get_prescription_scan_service() -> PrescriptionScanService:
    return _prescription_scan_service


def get_line_replier() -> LineReplier:
    return _line_replier


def get_lost_location_service() -> LostLocationService:
    return _lost_location_service


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


def get_content_preview_service() -> ContentPreviewService:
    """核准前的內容預覽服務；未設定 Firecrawl 時整條預覽路徑不可用。"""
    if _content_preview_service is None:
        raise HTTPException(status_code=503, detail="Content preview not configured")
    return _content_preview_service


def get_manual_report_quota() -> int:
    """手動知識回報的 24 小時配額上限。

    做成依賴而不是在 router 直接讀 settings，是為了讓測試用
    app.dependency_overrides 換成小值，不必 monkey patch Settings
    （tasks.md 4.3）。
    """
    return settings.KNOWLEDGE_REPORT_MANUAL_DAILY_QUOTA


def get_user_document_ingest_service() -> UserDocumentIngestService | None:
    """取得使用者上傳文件 ingest 服務；未設定 collection 時回傳 None。"""
    return _user_document_ingest_service


def get_user_document_answer_service() -> UserDocumentAnswerService | None:
    """取得使用者上傳文件問答服務；未設定 vector index 時回傳 None。"""
    return _user_document_answer_service


# ── 認證與家人權限 ─────────────────────────────────────────────────
from fastapi import Request  # noqa: E402 - 只有下面的頻率限制 dependency 用到

from app.core.rate_limit import RateLimiter, client_ip  # noqa: E402


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


def get_prescription_scan_enabled() -> bool:
    """讀取藥袋辨識功能開關目前的值。

    獨立成一支 dependency（而不是在呼叫端直接讀 settings.PRESCRIPTION_SCAN_ENABLED），
    是因為除了 require_prescription_scan_enabled 用它來決定要不要 404 之外，
    `GET /api/profiles/me/settings` 也要把這個布林值原樣回給 LIFF，讓前端在
    渲染掃描入口之前就能知道開關狀態，不必再用「探測一個不存在的草稿 ID、
    比對 404 錯誤訊息」這種依賴未受約束字串的方式旁敲側擊。兩處共用同一支
    dependency，測試也才能用 app.dependency_overrides 一次覆寫，不必動到
    settings 這個整個行程共用的單例。
    """
    return settings.PRESCRIPTION_SCAN_ENABLED


def require_prescription_scan_enabled(
    enabled: bool = Depends(get_prescription_scan_enabled),
) -> None:
    """功能開關關閉時，讓藥袋辨識相關端點表現得像不存在一樣，回 404。"""
    if not enabled:
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


# ── 請求頻率限制 ───────────────────────────────────────────────────
#
# 兩個工廠各回傳一支 dependency。**做成具名的模組層物件**（下面四個），路由
# 用 `Depends(liff_login_rate_limit)` 掛上；測試就能以 app.dependency_overrides
# 換掉或換成更小的上限，不必動 settings 這個整個行程共用的單例。
#
# 兩支都是 async def：同步 dependency 會被 FastAPI 丟到 threadpool，計數器
# 就得跨執行緒，沒必要。


def limit_by_client_ip(limiter: RateLimiter):
    """未登入端點：以來源 IP 計數（Cloudflare 標頭優先，見 rate_limit.client_ip）。"""

    async def dependency(request: Request) -> None:
        limiter.enforce(client_ip(request))

    dependency.limiter = limiter  # type: ignore[attr-defined] - 測試要 reset
    return dependency


def limit_by_user(limiter: RateLimiter):
    """已登入端點：以 line_user_id 計數。

    依賴 `get_current_user`，所以沒帶 token 的請求會先拿到 401、不進計數——
    未登入的濫用另有 IP 層的限制，這裡只管「同一個帳號」。
    """

    async def dependency(
        current_user: CurrentUser = Depends(get_current_user),
    ) -> None:
        limiter.enforce(current_user.line_user_id)

    dependency.limiter = limiter  # type: ignore[attr-defined]
    return dependency


liff_login_rate_limit = limit_by_client_ip(
    RateLimiter(limit=settings.RATE_LIMIT_LIFF_LOGIN_PER_MINUTE, window_seconds=60)
)
invite_verify_rate_limit = limit_by_client_ip(
    RateLimiter(limit=settings.RATE_LIMIT_INVITE_VERIFY_PER_MINUTE, window_seconds=60)
)
summary_generate_rate_limit = limit_by_user(
    RateLimiter(limit=settings.RATE_LIMIT_SUMMARY_GENERATE_PER_HOUR, window_seconds=3600)
)
prescription_scan_rate_limit = limit_by_user(
    RateLimiter(limit=settings.RATE_LIMIT_PRESCRIPTION_SCAN_PER_HOUR, window_seconds=3600)
)
lost_location_rate_limit = limit_by_user(
    RateLimiter(limit=settings.RATE_LIMIT_LOST_LOCATION_PER_MINUTE, window_seconds=60)
)

ALL_RATE_LIMITS = (
    liff_login_rate_limit,
    invite_verify_rate_limit,
    summary_generate_rate_limit,
    prescription_scan_rate_limit,
    lost_location_rate_limit,
)


def reset_rate_limits() -> None:
    """清空全部計數。給測試用：同一個行程跑上百個 HTTP 測試，同一個假使用者
    很快就會撞到每小時的上限，而那不是任何一個測試要驗的事。"""
    for dependency in ALL_RATE_LIMITS:
        dependency.limiter.reset()  # type: ignore[attr-defined]


_clinic_transcript_service: "ClinicTranscriptService | None" = None


def get_clinic_transcript_service() -> "ClinicTranscriptService":
    """看診錄音服務。

    第一次用到才建，而且 import 也延後到這裡：轉錄要 `google.genai` 的型別，
    而 `app/services/speech/audio.py` 開頭那段註解記著，頂端 import 大套件會讓
    backend／scheduler pod 啟動約 30 秒就被 OOMKilled。這個功能不是每個 pod
    都會用到，沒有理由讓它進到啟動路徑。
    """
    global _clinic_transcript_service
    if _clinic_transcript_service is None:
        from app.repositories.medication_repository import MedicationRepository
        from app.services.clinic_transcript.notifier import ClinicVisitNotifier
        from app.services.clinic_transcript.service import ClinicTranscriptService
        from app.services.clinic_transcript.summarizer import ClinicVisitSummarizer
        from app.services.speech.clinic_transcribe import ClinicTranscriber

        _clinic_transcript_service = ClinicTranscriptService(
            transcriber=ClinicTranscriber(),
            summarizer=ClinicVisitSummarizer(_gemini_service),
            medication_repository=MedicationRepository,
            notifier=ClinicVisitNotifier(
                replier=_line_replier,
                authorization_service=_family_authorization_service,
                user_profile_service=_user_profile_service,
                liff_url=settings.LIFF_URL,
            ),
        )
    return _clinic_transcript_service
