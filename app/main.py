import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from app.routers.line.webhook import router as line_router
from app.routers.liff.auth import router as auth_router
from app.routers.system import router as system_router
from app.routers.users.upsert_users import router as profile_router
from app.routers.users.consultations import router as consultations_router
from app.core.cors import add_cors_middleware
from app.core.config import settings, should_run_schedulers
from app.core.logging_setup import configure_logging
from app.core.upload_limits import MaxUploadSizeMiddleware
from app.dependencies import (
    get_consultation_service,
    get_chat_history_repository,
    get_drug_news_index_service,
    get_kb_digest_service,
    get_line_replier,
    get_user_profile_service,
    preload_facility_name_index,
    start_medication_scheduler,
    warm_rag_connections,
)
from app.repositories.consultation_repository import ConsultationRepository
from app.repositories.knowledge_report_repository import KnowledgeReportRepository
from app.repositories.medication_repository import MedicationLogRepository
from app.repositories.prescription_draft_repository import PrescriptionDraftRepository
from app.repositories.family_delegation_repository import FamilyDelegationRepository
from app.repositories.family_rbac_metrics_repository import FamilyRbacMetricsRepository
from app.repositories.family_role_audit_repository import FamilyRoleAuditRepository
from app.repositories.safety_alert_repository import SafetyAlertRepository
from app.services.medical_news.index_scheduler import (
    start_drug_news_index_scheduler,
)
from app.services.medical_news.push_scheduler import (
    start_medical_news_push_scheduler,
)
from app.repositories.medical_news_repository import (
    ensure_indexes as ensure_medical_news_indexes,
)
from app.services.consultation.scheduler import (
    start_consultation_daily_summary_scheduler,
)
from app.services.rag.user_document_store import ensure_user_docs_indexes_on_startup

from app.routers.users.family_tree import router as family_tree_router
from app.routers.users.knowledge_reports import router as knowledge_reports_router
from app.routers.users.medical import router as medical_router
from app.routers.users.medications import router as medications_router
from app.routers.admin.knowledge_reports import router as admin_knowledge_reports_router
from app.routers.tts.tts import router as tts_router
from app.routers.drug_appearance.images import router as drug_appearance_router

configure_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # FastAPI lifespan 會在 yield 前執行 startup 邏輯。
    # 先確認 MongoDB 摘要 collection 有 TTL index，讓摘要只保留 7 天。
    await ConsultationRepository.ensure_indexes()
    await KnowledgeReportRepository.ensure_indexes()
    # 用藥 log 的 (reminder_id, scheduled_at) 唯一索引：多實例並存時，
    # 它是「同一個時段只有一份 log」的唯一保證，推播權搶佔才有意義。
    await MedicationLogRepository.ensure_indexes()
    # 藥袋辨識草稿的 TTL 索引：草稿以 PRESCRIPTION_DRAFT_TTL_MINUTES 為存活
    # 時間，交由資料庫自動清除，應用端不需要另外排程刪除。
    await PrescriptionDraftRepository.ensure_indexes()
    # 用藥風險通報的節流索引：(user_id, drug_key) 的唯一約束是「同一個藥在
    # 節流視窗內只通報一次」的唯一保證，通報權就是靠它原子取得；expires_at
    # 的 TTL 讓視窗自動過期，不需要應用端排程清除。索引與功能開關無關，
    # 先備好才能在開關打開的當下就是正確行為。
    await SafetyAlertRepository.ensure_indexes()
    # 三個 collection 的索引一起建。(user_id, news_ref) 與
    # (recipient_id, news_ref) 兩個唯一索引同時承擔去重與推播權搶佔——
    # 少了它們，多實例並存時同一則消息會重複推播、家人分享會重複送達。
    await ensure_medical_news_indexes()
    # 家庭 RBAC 的三份新 collection。`family_rbac_metrics` 的 owner_id 唯一索引
    # 不只是查詢效率——差異計數是以 owner_id 為鍵的 upsert `$inc`，沒有唯一
    # 約束時併發會生出同一位擁有者的多份文件，遷移就緒的判讀就是錯的，而那正是
    # 決定何時對真實使用者開啟強制的依據。
    await FamilyDelegationRepository.ensure_indexes()
    await FamilyRoleAuditRepository.ensure_indexes()
    await FamilyRbacMetricsRepository.ensure_indexes()
    await ensure_user_docs_indexes_on_startup()

    # 預載院所名稱索引，供判斷使用者說的「診所／醫院／藥局」是專名還是泛稱。
    # 放在啟動而非對話路徑：名稱集合約 512 KB，載入一次即可，
    # 且意圖判定是同步函式，不適合在其中做非同步查詢。
    await preload_facility_name_index()

    # RAG 檢索連線的暖機。retriever 的 Mongo client 是首次查詢才懶建的，不在
    # 上面 ensure_indexes 那條路徑上，所以每次部署後第一個問問題的使用者要
    # 獨自付建立連線的成本（本機實測約 12 秒，之後穩態 50-250ms）。
    #
    # 刻意用背景 task 而非 await：擋在 lifespan 裡會讓 readiness probe 晚
    # 12 秒才通過，滾動更新期間反而更容易出現 502。暖機失敗不影響服務。
    warm_rag_task = asyncio.create_task(warm_rag_connections())

    # 背景排程器只在扮演 scheduler 角色的行程啟動。
    #
    # 拆開的理由：排程器原本與 API 共用同一個事件迴圈，因此 API 的每一次重新
    # 部署、OOM 或崩潰都會連帶重啟排程器，而 medication-reminders 的規格明訂
    # 「錯過時段不補推播」——重啟若橫跨某個服藥時段，那個時段就永久錯過。
    # 分開之後，改 API 不再影響提醒的準時性。
    #
    # 注意：拆開後**不代表**只會有一個排程器實例。滾動更新的策略是
    # maxUnavailable: 0 / maxSurge: 1（先起新的、ready 後才關舊的），每次
    # 部署都必然有一段時間兩個實例並存，因此 medication-reminders 既有的
    # 原子搶佔與 (reminder_id, scheduled_at) 唯一索引仍然是必要的，不得移除。
    run_schedulers = should_run_schedulers(settings.APP_ROLE)
    scheduler = None
    medication_scheduler = None
    news_index_scheduler = None
    news_push_scheduler = None

    if run_schedulers:
        # 啟動每日諮詢摘要排程
        scheduler = start_consultation_daily_summary_scheduler(
            enabled=True,  # 啟動自動排程
            run_time=settings.CONSULTATION_DAILY_SUMMARY_TIME,
            consultation_service=get_consultation_service(),
            consultation_store=get_chat_history_repository(),
        )

        # 啟動雙階遞進用藥提醒排程引擎
        medication_scheduler = start_medication_scheduler(
            enabled=True,
            replier=get_line_replier(),
            user_profile_service=get_user_profile_service(),
        )

        # 每日醫療消息卡。索引與推播是兩支獨立的排程：成本模型不同（前者是
        # O(不重複藥數)、後者是 O(使用者數)），失敗模式也不同——政府站台逾時
        # 會讓索引整輪失敗，但推播照常拿昨天的索引跑。合在一起時前者會拖垮
        # 後者。兩者的心跳因此也分開登記。
        news_index_scheduler = start_drug_news_index_scheduler(
            enabled=settings.MEDICAL_NEWS_ENABLED,
            index_service=get_drug_news_index_service(),
            run_time=settings.MEDICAL_NEWS_INDEX_TIME,
        )
        news_push_scheduler = start_medical_news_push_scheduler(
            enabled=settings.MEDICAL_NEWS_ENABLED and get_kb_digest_service() is not None,
            replier=get_line_replier(),
            user_profile_service=get_user_profile_service(),
            kb_digest=get_kb_digest_service(),
            run_time=settings.MEDICAL_NEWS_PUSH_TIME,
            max_age_days=settings.MEDICAL_NEWS_MAX_AGE_DAYS,
        )
    else:
        logger.info(
            "APP_ROLE=%s：本行程不啟動背景排程器（僅服務請求）", settings.APP_ROLE
        )

    try:
        # yield 期間代表 app 正在運行並處理 requests。
        yield
    finally:
        # 暖機還沒跑完就關機時收掉它，避免留下未處理的 task。
        if not warm_rag_task.done():
            warm_rag_task.cancel()
        # shutdown 時取消背景排程 tasks
        if scheduler is not None:
            await scheduler.stop()
        if medication_scheduler is not None:
            await medication_scheduler.stop()
        if news_index_scheduler is not None:
            await news_index_scheduler.stop()
        if news_push_scheduler is not None:
            await news_push_scheduler.stop()



app = FastAPI(
    title="CARE Backend API",
    description="CARE 系統後端 API (包含 LINE Bot Webhook 與 LIFF REST API)",
    version="1.0.0",
    lifespan=lifespan,
)


# 藥袋影像上傳的大小限制必須在 ASGI 層攔截，路由層檢查太晚——見
# app/core/upload_limits.py 開頭的說明。放在 add_cors_middleware 之前，
# 讓 CORS 之後被加入而成為最外層，這樣即使這裡直接短路回應（不經過下游），
# 回應仍會被 CORS 包住、帶有正確的 CORS 標頭。
app.add_middleware(
    MaxUploadSizeMiddleware,
    path="/api/medications/prescription-scan",
    method="POST",
    max_bytes=settings.PRESCRIPTION_SCAN_MAX_IMAGE_BYTES,
)

# Centralized CORS config
add_cors_middleware(app)
app.include_router(system_router)
app.include_router(line_router, prefix="/line", tags=["LINE Bot"])
app.include_router(profile_router, prefix="/api/profiles", tags=["Profile"])
app.include_router(
    consultations_router, prefix="/api/consultations", tags=["Consultation"]
)
app.include_router(auth_router, prefix="/api/auth", tags=["Auth"])
app.include_router(family_tree_router, prefix="/api/family", tags=["Family Tree"])
app.include_router(medications_router, prefix="/api/medications", tags=["Medications"])
# 前綴必須是 /api/medical：CARE-LIFF 的 medicalApi.ts 已經在打 /api/medical/nearby，
# 「附近醫院」整頁（路由、側邊欄、i18n）都已上線，缺的一直只有這一行掛載。
app.include_router(medical_router, prefix="/api/medical", tags=["Medical"])
app.include_router(
    knowledge_reports_router,
    prefix="/api/knowledge-reports",
    tags=["Knowledge Reports"],
)
app.include_router(
    admin_knowledge_reports_router,
    prefix="/api/admin/knowledge-reports",
    tags=["Knowledge Reports Admin"],
)
app.include_router(tts_router, prefix="/tts")
# 前綴直接讀 settings，不能像上面 tts_router 那樣寫死字面值：
# drug_appearance_image_service 組 URL 時就是用這個設定值，寫死會讓兩邊
# 在改了 env 之後兜不起來（掛載路徑沒變，組出來的 URL 卻變了），
# 縮圖看似正常產生實際上全部 404，而且不會有任何錯誤訊息。
app.include_router(
    drug_appearance_router,
    prefix="/" + settings.DRUG_APPEARANCE_IMAGE_URL_PATH.strip("/"),
)
