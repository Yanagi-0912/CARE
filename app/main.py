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
from app.core.startup_checks import openapi_visibility, validate_runtime_config
from app.core.logging_setup import configure_logging
from app.core.upload_limits import MaxUploadSizeMiddleware
from app.dependencies import (
    get_appointment_repository,
    get_consultation_service,
    get_conversation_log_repository,
    get_drug_news_index_service,
    get_family_authorization_service,
    get_kb_digest_service,
    get_line_replier,
    get_lost_location_service,
    get_user_profile_service,
    preload_facility_name_index,
    start_appointment_scheduler,
    start_medication_scheduler,
    warm_rag_connections,
)
from app.repositories.consultation_repository import ConsultationRepository
from app.repositories.conversation_log_repository import ConversationLogRepository
from app.repositories.knowledge_report_preview_repository import (
    KnowledgeReportPreviewRepository,
)
from app.repositories.knowledge_report_repository import KnowledgeReportRepository
from app.repositories.user_profile_repository import UserProfileRepository
from app.repositories.medication_repository import MedicationLogRepository
from app.repositories.prescription_draft_repository import PrescriptionDraftRepository
from app.repositories.clinic_recording_session_repository import (
    ClinicRecordingSessionRepository,
)
from app.repositories.clinic_transcript_repository import (
    ClinicTranscriptRepository,
)
from app.repositories.family_delegation_repository import FamilyDelegationRepository
from app.repositories.family_rbac_metrics_repository import FamilyRbacMetricsRepository
from app.repositories.family_role_audit_repository import FamilyRoleAuditRepository
from app.repositories.emergency_report_repository import EmergencyReportRepository
from app.repositories.health_alert_claim_repository import HealthAlertClaimRepository
from app.repositories.health_alert_threshold_repository import (
    HealthAlertThresholdRepository,
)
from app.repositories.health_measurement_repository import (
    HealthMeasurementRepository,
)
from app.repositories.menstrual_record_repository import MenstrualRecordRepository
from app.repositories.safety_alert_repository import SafetyAlertRepository
from app.repositories.step_session_repository import StepSessionRepository
from app.repositories.lost_session_repository import LostSessionRepository
from app.services.lost.lost_location_scheduler import start_lost_location_scheduler
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
from app.routers.users.health import router as health_router
from app.routers.users.knowledge_reports import router as knowledge_reports_router
from app.routers.users.medical import router as medical_router
from app.routers.users.medications import router as medications_router
from app.routers.users.appointments import router as appointments_router
from app.routers.users.lost import router as lost_router
from app.routers.users.clinic_transcripts import router as clinic_transcripts_router
from app.routers.admin.knowledge_reports import router as admin_knowledge_reports_router
from app.routers.tts.tts import router as tts_router
from app.routers.drug_appearance.images import router as drug_appearance_router

configure_logging()

logger = logging.getLogger(__name__)


async def ensure_indexes_or_log(label: str, ensure) -> None:
    """建索引；失敗只記 log，不讓啟動失敗。

    索引建不起來的常見原因是既有資料違反唯一約束（例如 users 已有重複的
    line_id）或 TTL 索引與既有同名索引選項衝突。這些都不是「服務不能跑」——
    沒有索引只是慢、或少了一層併發保護——但 lifespan 拋例外會讓 backend 與
    scheduler 兩個 pod 一起 CrashLoop，整站 502。維運要從這筆 exception log
    看到該修什麼，而不是從 pod 一直重啟猜。
    """
    try:
        await ensure()
    except Exception:
        logger.exception(
            "[startup] %s 的索引建立失敗，服務照常啟動；請依上方例外修正資料後重啟",
            label,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 認證設定不合格就不起來（預設 JWT 密鑰、缺 LINE 憑證、演算法不在白名單）。
    # 放在最前面、建索引之前：起不來的 pod 讓滾動更新停在舊版，比帶著開發密鑰
    # 服務正式流量安全得多。理由與各項檢查見 app/core/startup_checks.py。
    validate_runtime_config(settings)

    # FastAPI lifespan 會在 yield 前執行 startup 邏輯。
    # users 的 line_id 唯一索引：沒有它，首次登入的併發 upsert 會生出同一個人的
    # 多份文件。既有重複資料會讓它建不起來——先跑 scripts/dedupe_users_line_id.py
    # 清掉，下一次啟動就建得起來；建不起來的期間服務照跑，只是少了這層保護。
    await ensure_indexes_or_log("users", UserProfileRepository.ensure_indexes)
    # 摘要 collection 的 (line_id, summary_date) 查詢索引。
    await ensure_indexes_or_log(
        "consultation_summaries", ConsultationRepository.ensure_indexes
    )
    # 對話原文的正式紀錄：expires_at 的 TTL 索引負責 30 天的保存期限，
    # 應用端不需要另外排程刪除。
    await ensure_indexes_or_log(
        "conversation_messages", ConversationLogRepository.ensure_indexes
    )
    await ensure_indexes_or_log(
        "knowledge_reports", KnowledgeReportRepository.ensure_indexes
    )
    # 預覽快照的 TTL 索引。這一支以前沒有任何地方呼叫，快照永遠不會被回收。
    await ensure_indexes_or_log(
        "knowledge_report_previews", KnowledgeReportPreviewRepository.ensure_indexes
    )
    # 用藥 log 的 (reminder_id, scheduled_at) 唯一索引：多實例並存時，
    # 它是「同一個時段只有一份 log」的唯一保證，推播權搶佔才有意義。
    await MedicationLogRepository.ensure_indexes()
    # 掛號提醒三組查詢的索引（列表、三個推播階段、當日結束的掃描）。
    await get_appointment_repository().ensure_indexes()
    # 藥袋辨識草稿的 TTL 索引：草稿以 PRESCRIPTION_DRAFT_TTL_MINUTES 為存活
    # 時間，交由資料庫自動清除，應用端不需要另外排程刪除。
    await PrescriptionDraftRepository.ensure_indexes()
    # 看診錄音紀錄：expires_at 的 TTL（30 天，與對話原文同一條線）與
    # (user_id, recorded_at) 的清單索引。
    await ensure_indexes_or_log(
        "clinic_visit_records", ClinicTranscriptRepository.ensure_indexes
    )
    # 聊天室「等看診錄音」的狀態：一人一筆（recorder_id 唯一）、3 小時 TTL。
    await ensure_indexes_or_log(
        "clinic_recording_sessions", ClinicRecordingSessionRepository.ensure_indexes
    )
    # 用藥風險通報的節流索引：(user_id, drug_key) 的唯一約束是「同一個藥在
    # 節流視窗內只通報一次」的唯一保證，通報權就是靠它原子取得；expires_at
    # 的 TTL 讓視窗自動過期，不需要應用端排程清除。索引與功能開關無關，
    # 先備好才能在開關打開的當下就是正確行為。
    await SafetyAlertRepository.ensure_indexes()
    # 走失求救：「同一位長輩只有一次進行中」的部分唯一索引、scheduler 的兩個掃描、
    # 以及 24 小時後刪除位置紀錄的 TTL。建不起來時服務照跑，只是少了這層保護。
    await ensure_indexes_or_log("lost_sessions", LostSessionRepository.ensure_indexes)
    # 四個 collection 的索引一起建。(user_id, delivered_on)、(user_id, news_ref) 與
    # (recipient_id, news_ref) 三個唯一索引同時承擔去重與推播權搶佔——
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

    # 個人健康紀錄的五份新 collection（personal-health-tracking design.md
    # 「資料格式」段末：family-rbac 曾經漏掉「寫了 ensure_indexes 但沒有任何
    # 地方呼叫」這一步，索引因此在正式環境永遠不會存在，見
    # test_startup_creates_indexes_for_every_new_health_collection）。
    # health_alert_claims 的 (user_id, alert_key) 唯一索引加 expires_at TTL
    # 是推播節流的唯一保證，總開關關閉時也照常建立——開關只影響要不要推播，
    # 不影響索引要不要先備好。
    await HealthMeasurementRepository.ensure_indexes()
    await HealthAlertThresholdRepository.ensure_indexes()
    await MenstrualRecordRepository.ensure_indexes()
    await StepSessionRepository.ensure_indexes()
    await HealthAlertClaimRepository.ensure_indexes()
    # 緊急回報稽核：頻率限制靠 (reporter_id／patient_id, reported_at) 計數，60 天
    # 保存期限靠 expires_at 的 TTL。建不起來時照常通報，只是少了限流與自動刪除。
    await ensure_indexes_or_log("emergency_reports", EmergencyReportRepository.ensure_indexes)

    # 預載院所名稱索引，供判斷使用者說的「診所／醫院／藥局」是專名還是泛稱。
    # 放在啟動而非對話路徑：名稱集合約 512 KB，載入一次即可，
    # 且意圖判定是同步函式，不適合在其中做非同步查詢。
    await preload_facility_name_index()

    # RAG 檢索連線的暖機。retriever 的 Mongo client 是首次查詢才懶建的，不在
    # 上面 ensure_indexes 那條路徑上，所以每次部署後第一個問問題的使用者要
    # 獨自付建立連線的成本（健康網路下 0.7-0.9 秒，網路不佳時更久）。
    #
    # 刻意用背景 task 而非 await：擋在 lifespan 裡會讓 readiness probe 等它
    # 跑完才通過，網路不佳時那可能是數十秒，滾動更新期間反而更容易 502。
    # 暖機失敗不影響服務。
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
    appointment_scheduler = None
    news_index_scheduler = None
    news_push_scheduler = None
    lost_location_scheduler = None

    if run_schedulers:
        # 啟動每日諮詢摘要排程
        scheduler = start_consultation_daily_summary_scheduler(
            enabled=True,  # 啟動自動排程
            run_time=settings.CONSULTATION_DAILY_SUMMARY_TIME,
            consultation_service=get_consultation_service(),
            consultation_store=get_conversation_log_repository(),
        )

        # 啟動雙階遞進用藥提醒排程引擎
        medication_scheduler = start_medication_scheduler(
            enabled=True,
            replier=get_line_replier(),
            user_profile_service=get_user_profile_service(),
            # 逾時通報的家屬名單。與掛號、高風險藥物、緊急通報同一個 resolver。
            authorization_service=get_family_authorization_service(),
        )

        # 掛號提醒（T-1h／T+0／T+30、當日結束標記 missed）。與用藥共用排程骨架，
        # 但各自一個 task、各自一個心跳——一邊卡住不該拖著另一邊。
        appointment_scheduler = start_appointment_scheduler(enabled=True)

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
        # 走失求救：位置停止更新的通知與 2 小時自動結束。
        lost_location_scheduler = start_lost_location_scheduler(
            service=get_lost_location_service()
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
        if appointment_scheduler is not None:
            await appointment_scheduler.stop()
        if news_index_scheduler is not None:
            await news_index_scheduler.stop()
        if news_push_scheduler is not None:
            await news_push_scheduler.stop()
        if lost_location_scheduler is not None:
            await lost_location_scheduler.stop()



app = FastAPI(
    title="CARE Backend API",
    description="CARE 系統後端 API (包含 LINE Bot Webhook 與 LIFF REST API)",
    version="1.0.0",
    lifespan=lifespan,
    # /docs、/redoc、/openapi.json 只在 APP_ENV=development 開：整份端點與模型
    # 清單對未登入者攤開，是最省力的攻擊面盤點（見 startup_checks.openapi_visibility）。
    **openapi_visibility(settings.APP_ENV),
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

# 看診錄音的上傳上限，理由與上面那一段相同（路由層攔不住，必須在 ASGI 層）。
app.add_middleware(
    MaxUploadSizeMiddleware,
    path="/api/clinic-visits",
    method="POST",
    max_bytes=settings.CLINIC_RECORDING_MAX_BYTES,
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
app.include_router(health_router, prefix="/api/health", tags=["Health"])
app.include_router(appointments_router, prefix="/api/appointments", tags=["Appointments"])
app.include_router(lost_router, prefix="/api/lost", tags=["Lost Location"])
app.include_router(
    clinic_transcripts_router, prefix="/api/clinic-visits", tags=["Clinic Visits"]
)
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
# 正式環境的 /tts 由 ingress 導到 care-tts（app/tts_main.py），音檔存在它的 PVC；這裡的
# 掛載給沒設 TTS_SERVICE_URL、backend 自己合成的本機開發用。
app.include_router(tts_router, prefix="/tts")
# 前綴直接讀 settings，不能像上面 tts_router 那樣寫死字面值：
# drug_appearance_image_service 組 URL 時就是用這個設定值，寫死會讓兩邊
# 在改了 env 之後兜不起來（掛載路徑沒變，組出來的 URL 卻變了），
# 縮圖看似正常產生實際上全部 404，而且不會有任何錯誤訊息。
app.include_router(
    drug_appearance_router,
    prefix="/" + settings.DRUG_APPEARANCE_IMAGE_URL_PATH.strip("/"),
)
