# 諮詢功能的核心服務，負責處理諮詢訊息的摘要生成和搜尋等邏輯。
from __future__ import annotations

import json
from datetime import date, datetime, time, timezone
from textwrap import dedent
from typing import Optional
from app.models.chat_message import ChatMessage
from app.models.consultation import (
    ConsultationSummarizeRequest,
    ConsultationSummary,
)
from app.models.medication import SLOT_DISPLAY_NAMES, TAIPEI_TZ, ensure_aware_utc
from app.repositories.appointment_repository import AppointmentReminderRepository
from app.repositories.consultation_repository import ConsultationRepository
from app.repositories.conversation_log_repository import ConversationLogRepository
from app.services.gemini.services import GeminiService
from app.services.gemini.shared.errors import raise_mapped_gemini_error
from app.services.medication.medication_service import MedicationService
from app.services.users.user_profile_service import UserProfileService
from resources.flex_messages.medical_messages.emergency_condition_flex_message import (
    RISK_ALERT_KEY,
)

DEFAULT_SUMMARY_LANGUAGE = "zh-TW"
SUPPORTED_SUMMARY_LANGUAGES = {
    "zh-TW": "繁體中文",
    "en": "英文",
    "id": "印尼文",
    "vi": "越南文",
    "th": "泰文",
    "ja": "日文",
}

# 摘要欄位定義（統一英文 snake_case key，與 i18n 欄位名對照）
SUMMARY_FIELDS = {
    "health_issue": "string",
    "medications_and_appointments": "string",
    "recommendations": "string",
    "key_safety_alerts": "string",
    "other": "string",
    "ai_summary": "string",
}

RISK_ALERT_MARKER = "觸發風險警示"

# 掛號提醒只列摘要日當天起最近的幾筆，避免長期回診表把 prompt 撐大
APPOINTMENT_CONTEXT_LIMIT = 5
APPOINTMENT_STATUS_LABELS = {
    "scheduled": "已排定",
    "departed": "已出發",
    "attended": "已到診",
    "missed": "未到診",
}


def _build_summary_schema(language: str) -> dict:
    # JSON schema 用統一的英文 key；Gemini 回傳時會用這些 key，不再依語言變化
    return {
        "type": "object",
        "properties": {key: {"type": field_type} for key, field_type in SUMMARY_FIELDS.items()},
        "required": list(SUMMARY_FIELDS.keys()),
    }


def _risk_alert(message: ChatMessage) -> Optional[dict]:
    if message.message_type != "assistant_reply":
        return None
    if not message.content.lstrip().startswith("{"):
        return None
    try:
        payload = json.loads(message.content)
    except ValueError:
        return None
    alert = payload.get(RISK_ALERT_KEY) if isinstance(payload, dict) else None
    return alert if isinstance(alert, dict) else None


def _transcript_line(message: ChatMessage, previous: Optional[ChatMessage]) -> str:
    alert = _risk_alert(message)
    if alert is None:
        return f"[{message.message_type}] {message.content}"
    # 紅卡存的是整張卡片 JSON，換成家屬也看得懂的一行：原話緊接在紅卡前一則
    parts = [RISK_ALERT_MARKER]
    if previous is not None and previous.message_type != "assistant_reply":
        parts.append(f"使用者輸入：「{previous.content}」")
    reason = str(alert.get("reason") or "").strip()
    if reason:
        parts.append(f"判定原因：{reason}")
    return f"[{message.message_type}] " + "｜".join(parts)


def _taipei_date(timestamp: datetime) -> date:
    # 訊息時間戳存的是 UTC；「哪一天的對話」要以使用者所在的台北日期為準，
    # 否則台北 00:00–08:00 的對話會被算到前一天。
    return ensure_aware_utc(timestamp).astimezone(TAIPEI_TZ).date()


class ConsultationService:
    def __init__(
        self,
        *,
        # 對話原文的正式紀錄（Mongo，保存 30 天）。摘要與原始訊息查詢都讀這份，
        # 不讀 Redis——那只是 agent 用的快取，只留最近幾則、一天就過期。
        chat_history_repository: ConversationLogRepository,
        # repository 是儲存諮詢摘要的抽象介面，實際上由 MongoDBConsultationRepository 實作
        repository: ConsultationRepository,
        # 用來生成最後摘要
        gemini_service: GeminiService,
        # 讀取使用者偏好的語言
        user_profile_service: UserProfileService,
        # 摘要時附上系統裡設定的用藥與掛號提醒
        medication_service: MedicationService,
        appointment_repository: AppointmentReminderRepository,
    ) -> None:
        self._chat_history_repository = chat_history_repository
        self._repository = repository
        self._gemini_service = gemini_service
        self._user_profile_service = user_profile_service
        self._medication_service = medication_service
        self._appointment_repository = appointment_repository

    # 這裡的 get_view 方法會優先嘗試從 repository 取得特定的摘要，如果沒有傳入日期則取得最新摘要。
    async def get_view(
        self, user_id: str, target_date: Optional[date] = None
    ) -> Optional[ConsultationSummary]:
        if target_date is None:
            return await self._repository.get_latest_summary(user_id)
        return await self._repository.get_summary_by_date(user_id, target_date)

    # get_raw_view 方法則是直接從 chat_history_repository 取得原始訊息列表，不考慮是否有摘要。
    async def get_raw_view(
        self, user_id: str, target_date: Optional[date] = None
    ) -> list[ChatMessage]:
        messages = await self._chat_history_repository.list_messages(user_id)
        if not messages:
            return []
        if target_date is None:
            # LIFF 的原始紀錄頁把訊息攤成一整串、沒有日期標示；原文存 30 天，
            # 全部回傳會變成分不清哪天的長串，所以只給最近有對話的那一天。
            target_date = max(_taipei_date(message.timestamp) for message in messages)
        return [
            message
            for message in messages
            if _taipei_date(message.timestamp) == target_date
        ]

    async def get_all_summaries(self, user_id: str) -> list[ConsultationSummary]:
        return await self._repository.get_all_summaries(user_id)

    # 摘要使用者最近一次對話所在的那一天（台北日期）。
    async def summarize(
        self, user_id: str, request: ConsultationSummarizeRequest
    ) -> ConsultationSummary:
        messages = await self._chat_history_repository.list_messages(user_id)
        if messages:
            latest_message = max(
                messages, key=lambda message: ensure_aware_utc(message.timestamp)
            )
            target_date = _taipei_date(latest_message.timestamp)
        else:
            target_date = datetime.now(TAIPEI_TZ).date()
        return await self._summarize_date(
            user_id, target_date, messages, force=request.force
        )

    # 每日排程用：Redis 裡還有訊息的每個台北日期都補摘要（已是最新的會直接沿用）。
    # 不能只摘最新那天——TTL 每寫一則就重設，列表可能跨好幾天，前一天稍晚的
    # 對話若只在最新那天被看到，就永遠不會進到它自己那天的摘要。
    async def summarize_pending_dates(self, user_id: str) -> list[ConsultationSummary]:
        messages = await self._chat_history_repository.list_messages(user_id)
        days = sorted({_taipei_date(message.timestamp) for message in messages})
        return [
            await self._summarize_date(user_id, day, messages, force=False)
            for day in days
        ]

    async def _summarize_date(
        self,
        user_id: str,
        target_date: date,
        messages: list[ChatMessage],
        *,
        force: bool,
    ) -> ConsultationSummary:
        # Redis 列表的 TTL 每寫一則就重設，天天聊天的人會跨好幾天，只取當天的
        day_messages = [
            message
            for message in messages
            if _taipei_date(message.timestamp) == target_date
        ]

        existing = await self._repository.get_summary_by_date(user_id, target_date)
        if existing is not None and not force:
            # 摘要產生後當天又有新對話就重做，否則後面的對話永遠不會被摘要
            summarized_at = ensure_aware_utc(existing.created_at)
            if all(
                ensure_aware_utc(message.timestamp) <= summarized_at
                for message in day_messages
            ):
                return existing

        language = await self.resolve_summary_language(user_id)
        summary_text = await self._generate_summary(
            user_id,
            target_date,
            day_messages,
            language=language,
        )
        summary = ConsultationSummary(
            line_id=user_id,
            summary_date=target_date,
            summary=summary_text,
            language=language,
            created_at=datetime.now(timezone.utc),
        )
        await self._repository.upsert_summary(summary)
        return summary

    # 摘要與下載檔的檔頭都用使用者目前的語言設定
    async def resolve_summary_language(self, user_id: str) -> str:
        settings = await self._user_profile_service.get_user_settings(user_id)
        language = (settings or {}).get("language")
        if language not in SUPPORTED_SUMMARY_LANGUAGES:
            return DEFAULT_SUMMARY_LANGUAGE
        return language

    @staticmethod
    def _language_name(language: str) -> str:
        return SUPPORTED_SUMMARY_LANGUAGES.get(
            language, SUPPORTED_SUMMARY_LANGUAGES[DEFAULT_SUMMARY_LANGUAGE]
        )

    # 真正呼叫 gemini 做摘要
    async def _generate_summary(
        self,
        user_id: str,
        target_date: date,
        messages: list[ChatMessage],
        *,
        language: str,
    ) -> str:
        if not messages:
            return "該日期尚無諮詢記錄。"

        language_name = self._language_name(language)
        # 對話稿：每則訊息逐行排版，避免 list repr 造成模型誤解
        transcript_lines = "\n".join(
            _transcript_line(message, messages[index - 1] if index else None)
            for index, message in enumerate(messages)
        )

        reminder_context = await self._build_reminder_context(user_id, target_date)
        prompt = dedent(f"""
            你是醫療諮詢摘要助手。
            請根據對話輸出 JSON。
            使用者資料庫語言：{language}（{language_name}）
            請以該語言撰寫各欄位內容。

            JSON 欄位 key 固定為英文 snake_case（不隨語言改變）：
            - health_issue: 使用者本人的健康問題
            - medications_and_appointments: 用藥與掛號紀錄
            - recommendations: AI 提供的建議
            - key_safety_alerts: 關鍵安全提醒與危急情況
            - other: 其他有幫助的背景資訊
            - ai_summary: 1-3 句話的諮詢重點摘要

            【最重要原則】
            你不是在建立「對話中出現過的醫療名詞清單」，而是在建立「本次諮詢的使用者可讀摘要」。
            寧可省略對本次諮詢沒有實質幫助的資訊，也不要為了完整而列出大量醫療名詞。
            任何資訊在放入摘要前，都必須先判斷：
            1. 是否為使用者本人提供的資訊？
            2. 是否與本次諮詢有關？
            3. 是否為使用者目前正在發生的事情、過去事情、一般性問題、假設情境或他人情況？
            4. 是否對理解本次諮詢有實質幫助？
            若答案不足以支持該資訊，請不要自行推論，直接省略或標示為「未確認」。
            不要因為某個疾病、症狀、藥物或醫療名詞曾經在對話中出現，就自動將其視為使用者本人目前的健康狀況。
            不要為了讓欄位看起來完整而填入不重要的資訊。

            【健康問題】
            「健康問題」是使用者本人在本次諮詢中明確描述、正在處理、關心或詢問的健康相關問題。
            健康問題可以包含：
            - 使用者本人明確描述的身體或心理症狀、不適或異常
            - 使用者本人目前正在經歷的疾病或健康狀況
            - 使用者針對自身疾病、症狀或健康狀況提出的疑問
            - 使用者針對自身用藥、保健食品或治療提出的疑問
            - 使用者針對自身檢查結果或健康風險提出的疑問
            - 只有使用者明確表示事件發生在自己身上時，才能將該事件視為使用者本人的健康問題；單純詢問某事件的醫療知識，不得視為該事件發生在使用者身上。

            只有在對話內容足以確認與使用者本人有關時，才能列入「健康問題」。
            例如：
            - 「我咳嗽三天」→ 健康問題：咳嗽
            - 「我最近一直噁心、嘔吐」→ 健康問題：噁心、嘔吐
            - 「我有腎臟病，想知道每天喝發泡錠會不會增加腎結石風險」→ 健康問題：發泡錠與腎結石風險
            - 「我昨天車禍後手一直流血」→ 健康問題：車禍後出血
            - 「我朋友一直咳嗽」→ 不可列入使用者本人的健康問題
            - 「咳嗽可能是什麼原因？」→ 若未表示與自身有關，不可列入使用者本人的健康問題
            - 「如果喝農藥會有什麼症狀？」→ 不可列為使用者本人喝農藥或中毒
            - 「如果有人被槍指著該怎麼辦？」→ 不可列為使用者本人遭遇危險
            健康問題不是醫療名詞清單。
            若同一天有多個彼此獨立的重要健康問題，可以列出多個項目，但應合併相近或重複內容，只保留對理解本次諮詢有實質幫助的資訊。
            若無法確認某項健康問題是否與使用者本人有關，請不要自行推論；必要時標示「未確認是否為使用者本人狀況」。

            【用藥與掛號紀錄】
            僅記錄使用者本人在本次諮詢中明確提到的用藥情形、服藥疑問、藥物名稱、看診安排、診所/醫院門診與掛號相關資訊。
            不得根據疾病、症狀或 AI 建議自行推測使用者曾進行某項看診或用藥。
            例外：文末「系統記錄的用藥與掛號提醒」是使用者或家屬在系統中設定的提醒，不是本次對話內容。請一併整理進本欄並標明為系統提醒；不得當成使用者今天說過的話，也不得據此推論使用者已經服藥或已經就診。
            若沒有相關資訊，填寫「無」。

            【建議】
            僅整理 AI 在本次對話中實際提供的核心行動建議，不得根據自己的醫療知識新增建議。
            只保留對使用者具有實際行動價值的主要建議，刪除重複、次要或一般性敘述。
            建議應盡可能反映 AI 實際對使用者提出的處理方式、就醫建議、注意事項或下一步行動。
            若 AI 沒有提供明確建議，填寫「無」。

            【關鍵情況與安全提醒】
            僅整理本次對話中實際具有優先處理價值的安全相關資訊。
            必須區分：
            1. 使用者明確表示目前正在發生
            2. 使用者表示過去曾發生
            3. 使用者詢問一般性處置
            4. 使用者提出假設情境
            5. 無法確認是否正在發生
            不得僅因對話中出現「自殺」、「中毒」、「大出血」、「車禍」等高風險詞彙，就判定使用者目前正在經歷該事件。
            若使用者明確表示危急情況正在發生，應優先整理該情況及 AI 提供的相關安全建議。
            若使用者僅提出一般性問題或假設情境，不得將其描述為使用者本人正在發生的危急事件。
            若無法確認是否正在發生，必須明確標示「未確認是否為目前正在發生」，不可自行推論。
            對話稿中標示「{RISK_ALERT_MARKER}」的那一行，代表系統當下判定需要立即處置，已送出緊急求助卡片。這類事件一律列入本欄，不得省略或淡化：寫出「{RISK_ALERT_MARKER}」（以{language_name}表達）、使用者當時輸入的原話與判定原因，讓使用者與家屬一看就懂發生了什麼。例如：{RISK_ALERT_MARKER}：使用者輸入「我要自殺」（判定原因：表達想結束生命）。
            若沒有值得特別提醒的安全相關資訊，填寫「無」。

            【其他】
            僅記錄「對理解本次諮詢有幫助，但無法歸入健康問題、用藥與掛號紀錄、建議或關鍵情況與安全提醒」的重要背景資訊。
            例如：
            - 與本次問題相關的重要既往病史
            - 使用者明確提供、且會影響本次健康諮詢的重要背景
            - 與本次諮詢直接相關的重要時間、情境或背景
            不可將以下內容放入「其他」：
            - 對本次諮詢沒有實質幫助的聊天內容
            - AI 自行推測的資訊
            - 僅因為對話中曾出現就保留下來的醫療名詞
            - 與本次諮詢無關的疾病、症狀或事件
            - 已經可以歸入其他欄位的資訊
            如果某項資訊對理解本次諮詢沒有實際幫助，即使它出現在對話中，也應捨棄。

            【AI小摘要】
            用 1 到 3 句話總結本次諮詢的整體重點。只整理對話中已提過的核心內容，禁止自行編造。
            必須遵守以下規則：
            1. 「只能整理對話中已明確提過的資訊」——不得根據疾病名稱、症狀或 AI 回覆中的一般醫療知識推測使用者需要什麼建議。
            2. 「禁止自行新增對話中未提過的醫療建議」——例如對話中沒有提「應該掛家醫科」，就不能在小摘要加上這句話。
            3. 「禁止基於症狀推測診斷」——例如即使使用者提到咳嗽、發燒，也不能自行推測為感冒並建議「應就醫檢查」（除非 AI 在對話中明確提過）。
            4. 「優先呈現對話中已確認的重點」：使用者主動提出的問題、AI 實際提供的建議、對話中明確提到的危急情況。
            5. 「完整性 vs. 準確性」：寧可遺漏對話中的內容，也不要編造對話中沒有的建議。

            範例（禁止編造）：
            - ❌ 對話：「我最近一直頭痛」; AI小摘要說：「建議掛神經科」(對話中沒提科別)
            - ✓ 對話：「我最近一直頭痛，考慮去看醫生」; AI小摘要說：「使用者頭痛並計劃就醫」

            AI小摘要不需要逐一重複各欄位內容，應將對話中已提過的核心資訊整合成自然、簡潔且容易理解的整體摘要。

            【一般資訊與假設情境】
            使用者提出一般性醫療知識問題、假設情境、他人狀況或轉述內容時：
            - 不得將其當成使用者本人的健康問題。
            - 不得將假設事件當成目前正在發生的事件。
            - 若該問題本身是本次諮詢的重要主題，可以在「AI小摘要」中以「一般性詢問」、「假設情境詢問」等方式描述；除非能確認與使用者本人有關，否則不要放入「健康問題」。
            - 若對理解本次諮詢沒有實質幫助，直接省略。

            【輸出規則】
            - 每個欄位都必須是可直接閱讀的{language_name}字串
            - 沒有資料的欄位填寫「無」
            - 若某欄位有多個項目，使用「、」分隔成單一字串
            - 不得自行補充對話中不存在的資訊
            - 不得將 AI 回覆中的推測或一般醫療知識當成使用者本人的事實
            - 不得為了完整而保留與本次諮詢無關的資訊

            日期：{target_date.isoformat()}

            對話：
            {transcript_lines}

            系統記錄的用藥與掛號提醒：
            {reminder_context}
            """).strip()

        try:
            # 強制結構化輸出：Gemini 一定回傳 JSON 物件，不會包 markdown 圍欄
            schema = _build_summary_schema(language)
            result_dict = await self._gemini_service.invoke_structured_output(
                prompt=prompt,
                json_schema=schema,
            )
            # result_dict 已經是解析好的 Python dict，轉回 JSON 字串
            summary_text = json.dumps(result_dict, ensure_ascii=False)
        except Exception as exc:
            raise_mapped_gemini_error(exc)
        return summary_text or "該日期尚無可摘要內容。"

    async def _build_reminder_context(self, user_id: str, target_date: date) -> str:
        # 查詢失敗要往上拋：吞掉的話摘要會悄悄少了提醒，卻看起來一切正常
        day = target_date.isoformat()
        reminders = await self._medication_service.get_user_reminders_with_medications(
            user_id
        )
        medication_lines = []
        for reminder in sorted(reminders, key=lambda r: r.scheduled_time):
            if not reminder.enabled or reminder.start_date > day:
                continue
            if reminder.end_date is not None and reminder.end_date < day:
                continue
            names = "、".join(
                medication.name for medication in reminder.medications if medication.enabled
            )
            medication_lines.append(
                f"- {SLOT_DISPLAY_NAMES[reminder.slot_type]} {reminder.scheduled_time}："
                f"{names or '未指定藥品'}"
            )

        day_start = datetime.combine(target_date, time.min, tzinfo=TAIPEI_TZ)
        appointments = await self._appointment_repository.list_by_user(
            user_id, day_end_after=day_start
        )
        appointment_lines = [
            f"- {appointment.local_appointment_at:%Y-%m-%d %H:%M} "
            f"{appointment.hospital_name} {appointment.department}"
            f"（{APPOINTMENT_STATUS_LABELS[appointment.status]}）"
            for appointment in appointments
            if appointment.status != "cancelled"
        ][:APPOINTMENT_CONTEXT_LIMIT]

        return "\n".join(
            ["用藥提醒：", *(medication_lines or ["無"]), "掛號提醒：", *(appointment_lines or ["無"])]
        )
