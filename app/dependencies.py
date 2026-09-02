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
from app.repositories.knowledge_report_preview_repository import (
    KnowledgeReportPreviewRepository,
)
from app.repositories.knowledge_report_repository import KnowledgeReportRepository
from app.repositories.medication_repository import (
    MedicationRepository,
    MedicationReminderRepository,
)
from app.repositories.prescription_draft_repository import PrescriptionDraftRepository
from app.repositories.safety_alert_repository import SafetyAlertRepository
from app.repositories.user_profile_repository import UserProfileRepository
from app.services.agent.agent import Agent
from app.services.consultation.consultation_service import ConsultationService
from app.services.family.family_authorization_service import (
    FamilyAuthorizationService,
)
from app.services.family.family_delegation_service import (
    FamilyDelegationService,
)
from app.services.family.family_role_service import FamilyRoleService
from app.services.family.family_tree_service import FamilyTreeService
from app.services.medication.drug_appearance_image_service import (
    resolve_drug_appearance_image_url,
)
from app.services.medication.drug_catalog_service import DrugCatalogService
from app.services.medication.drug_indication_service import DrugIndicationService
from app.services.medication.medication_service import MedicationService
from app.services.medication.medication_scheduler import start_medication_scheduler
from app.services.medication.prescription_ocr_service import PrescriptionOcrService
from app.services.medication.prescription_scan_service import PrescriptionScanService
from app.services.safety.drug_mention_extractor import DrugMentionExtractor
from app.services.safety.ingredient_overlap import (
    IngredientWatchlist,
    load_local_action_forms,
)
from app.services.safety.otc_alert_service import OtcAlertService
from app.services.safety.safety_alert_service import SafetyAlertService
from app.services.gemini import GeminiService
from app.services.guardrail import GuardrailService
from app.services.history.history_service import LineMessageHistoryService
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
from app.services.rag.claim_verification.identity import GeminiClaimIdentityVerifier
from app.services.rag.claim_verification.matcher import MongoAtlasClaimMatcher
from app.services.rag.claim_verification.normalizer import GeminiClaimNormalizer
from app.services.rag.claim_verification.service import ClaimVerificationService
from app.services.rag.cohere_reranker import CohereReranker, VectorScoreReranker
from app.services.rag.firecrawl_client import FirecrawlClient
from app.services.rag.ingest_service import IngestService
from app.services.rag.whitelist import default_url_policy
from app.services.rag.user_document_answer_service import UserDocumentAnswerService
from app.services.rag.user_document_ingest_service import UserDocumentIngestService
from app.services.rag.user_document_retriever import UserDocumentVectorRetriever
from app.services.rag.query_rewriter import GeminiQueryRewriter
from app.services.rag.retrieval_grader import GeminiRetrievalGrader
from app.services.rag.web_search_service import WebSearchService
from app.services.users.user_profile_service import UserProfileService
from app.tools.claim_tools import configure_claim_tool
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
        title_field=settings.MONGODB_TEXT_TITLE_FIELD,
        title_boost=settings.RAG_TEXT_TITLE_BOOST,
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
    degraded_min_score=settings.RAG_DEGRADED_MIN_SCORE,
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
else:
    logger.info("CLAIM_VERIFICATION_ENABLED=false; verify_claim tool not configured")

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
_otc_local_action_forms = load_local_action_forms()
_otc_alert_service = OtcAlertService(
    catalog_service=_drug_catalog_service,
    medication_repository=MedicationRepository,
    reminder_repository=MedicationReminderRepository,
    replier=_line_replier,
    watchlist=_otc_watchlist,
    local_action_forms=_otc_local_action_forms,
    # 與高風險通報走同一個決策點，只是查 NOTIFICATION_POLICY 裡的另一個種類
    # （otc_medication_added）。收到通知 SHALL NOT 改變收件人的資料存取權。
    authorization_service=_family_authorization_service,
    user_profile_service=_user_profile_service,
)
_enabled_otc_alert_service = (
    _otc_alert_service if settings.OTC_ALERT_ENABLED else None
)

_message_handler = LineMessageHandler(
    agent=_care_agent,
    history_service=_line_history_service,
    user_profile_service=_user_profile_service,
    replier=_line_replier,
    loading_animation_service=_line_loading_animation_service,
    safety_alert_service=_enabled_safety_alert_service,
)
_media_handler = LineMediaHandler(
    agent=_care_agent,
    history_service=_line_history_service,
    user_profile_service=_user_profile_service,
    replier=_line_replier,
    loading_animation_service=_line_loading_animation_service,
    user_document_ingest_service=_user_document_ingest_service,
    safety_alert_service=_enabled_safety_alert_service,
)
_location_handler = LineLocationHandler(
    agent=_care_agent,
    history_service=_line_history_service,
    user_profile_service=_user_profile_service,
    replier=_line_replier,
    loading_animation_service=_line_loading_animation_service,
)
_family_tree_service = FamilyTreeService()
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


def get_tts_service() -> TTSService:
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
