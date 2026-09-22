"""在 LINE 聊天室裡錄看診，不經過 LIFF（2026-09-22 James 決定）。

LIFF 版的死穴是 LINE 內建瀏覽器能不能用麥克風（iOS 尤其沒把握），而聊天室本來就有
錄音鍵，長輩也本來就會傳語音。代價是聊天室沒有頁面可以放狀態，所以流程拆成：

1. 開始：掛號提醒「我已出發」卡片上的「看診時錄音」（postback），或直接打／說
   「看診錄音」。建一筆等待狀態（`ClinicRecordingSessionRepository`）。
2. 徵詢：快速回覆「醫師同意錄音／我出來自己講／不錄了」。衛福部規範要求錄音前
   徵得醫師同意，所以這一步不能省，也沒有預設值（理由同 LIFF 版的 consent 欄位）。
3. 下一則語音或音檔就是看診錄音：下載、建紀錄、背景轉錄，整理好由 notifier 推播。

沒有先按「看診錄音」就傳了一段很長的錄音，多半是忘了先按：先問要不要整理，
而不是把十分鐘的看診錄音當成一個問題丟給 agent。

目前只替自己錄。家人陪診替長輩錄（LIFF 版有「替誰看」）還沒搬過來。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlencode

from linebot.v3.messaging import PostbackAction, QuickReply, QuickReplyItem, TextMessage

from app.core.config import settings
from app.core.request_logging import log_stage
from app.i18n import t
from app.models.clinic_transcript import ConsentMode
from app.repositories.clinic_recording_session_repository import (
    ClinicRecordingSession,
    ClinicRecordingSessionRepository,
)

logger = logging.getLogger(__name__)

START_ACTION = "clinic_record_start"
CONSENT_ACTION = "clinic_record_consent"
CANCEL_ACTION = "clinic_record_cancel"
NOT_VISIT_ACTION = "clinic_record_not_visit"

_CONSENT_MODES: tuple[ConsentMode, ...] = ("doctor_agreed", "self_recap")

# 沒先按「看診錄音」的語音，多長才當成可能是看診錄音。聊天室的語音問題十幾秒上下；
# 三分鐘的「問題」幾乎不存在，而門診再短也有幾分鐘。
LONG_VOICE_MS = 3 * 60 * 1000
# 分享進來的音檔沒有 duration，用大小估：手機錄音程式常見的 AAC 單聲道約 64 kbps，
# 1 MB 約兩分鐘。
LONG_AUDIO_FILE_BYTES = 1_000_000

# 分享進來的音檔，LINE 回的 Content-Type 可能是 audio/*、video/mp4（m4a 常被這樣標）
# 或 application/octet-stream。下載後由 PyAV 看內容解碼，副檔名與 MIME 都只是參考。
_FILE_MIME_PREFIXES = ("audio/", "video/", "application/")

# 快速回覆按鈕字的上限（LINE 規格 20 字）。
_LABEL_MAX = 20

Downloader = Callable[..., Path]
Spawner = Callable[[Awaitable[None]], Any]


def postback_data(action: str, **params: str) -> str:
    return urlencode({"action": action, **{k: v for k, v in params.items() if v}})


class ClinicRecordingFlow:
    def __init__(
        self,
        *,
        service_provider: Callable[[], Any],
        replier: Any,
        sessions: Any = ClinicRecordingSessionRepository,
        download: Optional[Downloader] = None,
        spawn: Optional[Spawner] = None,
    ) -> None:
        # 用到才建：看診錄音服務會載入 google.genai，不能進 backend 的啟動路徑
        # （見 dependencies.get_clinic_transcript_service）。
        self._service_provider = service_provider
        self._replier = replier
        self._sessions = sessions
        self._download = download
        self._spawn = spawn or self._spawn_background
        # create_task 只留弱參照，背景轉錄跑到一半可能被回收；自己握著直到做完。
        self._tasks: set[asyncio.Task] = set()

    # ---- 開始與徵詢 ----

    async def start(
        self,
        recorder_id: str,
        reply_token: str,
        language: str,
        *,
        appointment_id: str = "",
        hospital_name: str = "",
        department: str = "",
    ) -> None:
        await self._sessions.save(
            ClinicRecordingSession(
                recorder_id=recorder_id,
                user_id=recorder_id,
                appointment_id=appointment_id or None,
                hospital_name=hospital_name,
                department=department,
            )
        )
        log_stage(logger, "clinic_chat", step="start", from_appointment=bool(appointment_id))
        await self._ask_consent(reply_token, recorder_id, language, "clinic.chat.ask_consent")

    async def choose_consent(
        self, recorder_id: str, reply_token: str, language: str, consent: str
    ) -> None:
        if consent not in _CONSENT_MODES:
            logger.warning("clinic consent postback 帶了不認得的值：%s", consent)
            return
        session = await self._sessions.get(recorder_id)
        if session is None:
            await self._reply(reply_token, recorder_id, t("clinic.chat.expired", language))
            return
        session = session.model_copy(update={"consent": consent, "step": "awaiting_audio"})
        log_stage(logger, "clinic_chat", step="consent", consent=consent)
        if session.pending_message_id:
            # 錄音先到、同意後到：現在就開始整理那一段。
            await self._begin(
                session,
                session.pending_message_id,
                session.pending_file_name,
                reply_token,
                language,
            )
            return
        await self._sessions.save(session)
        await self._reply(
            reply_token,
            recorder_id,
            t(f"clinic.chat.instructions.{consent}", language),
            quick_reply=self._quick_reply([(CANCEL_ACTION, "clinic.chat.qr.cancel", {})], language),
        )

    async def cancel(
        self, recorder_id: str, reply_token: str, language: str, *, not_visit: bool = False
    ) -> None:
        await self._sessions.delete(recorder_id)
        log_stage(logger, "clinic_chat", step="not_visit" if not_visit else "cancel")
        key = "clinic.chat.not_visit" if not_visit else "clinic.chat.cancelled"
        await self._reply(reply_token, recorder_id, t(key, language))

    # ---- 收到錄音 ----

    async def handle_audio(
        self,
        recorder_id: str,
        reply_token: str,
        language: str,
        *,
        message_id: str,
        is_file: bool,
        file_name: Optional[str] = None,
        duration_ms: Optional[int] = None,
        file_size: Optional[int] = None,
    ) -> bool:
        """這則語音／音檔若屬於看診錄音就接手並回 True；不是就回 False，照常當問題。"""
        session = await self._sessions.get(recorder_id)
        if session is not None and session.step == "awaiting_audio":
            await self._begin(session, message_id, file_name, reply_token, language)
            return True

        if session is None and not self._looks_like_a_visit(is_file, duration_ms, file_size):
            return False

        # 按了「看診錄音」但還沒回答同意與否，或沒按就傳了長錄音：先問，錄音記著。
        session = (session or ClinicRecordingSession(recorder_id=recorder_id, user_id=recorder_id)).model_copy(
            update={"pending_message_id": message_id, "pending_file_name": file_name}
        )
        await self._sessions.save(session)
        log_stage(logger, "clinic_chat", step="ask_after_audio", file=is_file)
        await self._ask_consent(
            reply_token, recorder_id, language, "clinic.chat.ask_is_visit", not_visit=True
        )
        return True

    @staticmethod
    def _looks_like_a_visit(
        is_file: bool, duration_ms: Optional[int], file_size: Optional[int]
    ) -> bool:
        if is_file:
            return (file_size or 0) >= LONG_AUDIO_FILE_BYTES
        return (duration_ms or 0) >= LONG_VOICE_MS

    async def _begin(
        self,
        session: ClinicRecordingSession,
        message_id: str,
        file_name: Optional[str],
        reply_token: str,
        language: str,
    ) -> None:
        # 先刪狀態：不論下載成功與否，這一輪都結束了，下一則語音不該再被攔。
        await self._sessions.delete(session.recorder_id)
        assert session.consent is not None, "_begin 只在選過同意方式之後呼叫"
        from app.services.media.mutimedia_processor import (
            MediaTooLargeError,
            media_processor_service,
        )

        download = self._download or media_processor_service._download_media_to_tmp
        is_file = bool(file_name)
        try:
            audio_path = await asyncio.to_thread(
                download,
                message_id,
                "file" if is_file else "audio",
                file_name,
                settings.CLINIC_RECORDING_MAX_BYTES,
                _FILE_MIME_PREFIXES if is_file else None,
            )
        except MediaTooLargeError:
            await self._reply(
                reply_token,
                session.recorder_id,
                t("clinic.chat.too_large", language).format(
                    limit_mb=settings.CLINIC_RECORDING_MAX_BYTES // (1024 * 1024)
                ),
            )
            return
        except Exception as exc:  # noqa: BLE001 - 下載失敗就請他再傳一次
            logger.warning("stage=clinic_chat 下載錄音失敗：%s", type(exc).__name__)
            await self._reply(reply_token, session.recorder_id, t("clinic.chat.download_failed", language))
            return

        service = self._service_provider()
        record = await service.start(
            user_id=session.user_id,
            created_by_user_id=session.recorder_id,
            consent=session.consent,
            appointment_id=session.appointment_id,
            hospital_name=session.hospital_name,
            department=session.department,
        )
        self._spawn(service.process(record, audio_path))
        log_stage(
            logger, "clinic_chat", step="received", record=record.id, consent=session.consent,
            bytes=audio_path.stat().st_size if audio_path.exists() else 0,
        )
        await self._reply(reply_token, session.recorder_id, t("clinic.chat.received", language))

    def _spawn_background(self, work: Awaitable[None]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ---- 回覆 ----

    async def _ask_consent(
        self,
        reply_token: str,
        recorder_id: str,
        language: str,
        key: str,
        *,
        not_visit: bool = False,
    ) -> None:
        items = [
            (CONSENT_ACTION, "clinic.chat.qr.doctor_agreed", {"mode": "doctor_agreed"}),
            (CONSENT_ACTION, "clinic.chat.qr.self_recap", {"mode": "self_recap"}),
            (NOT_VISIT_ACTION, "clinic.chat.qr.not_visit", {})
            if not_visit
            else (CANCEL_ACTION, "clinic.chat.qr.cancel", {}),
        ]
        await self._reply(
            reply_token, recorder_id, t(key, language), quick_reply=self._quick_reply(items, language)
        )

    @staticmethod
    def _quick_reply(
        items: list[tuple[str, str, dict[str, str]]], language: str
    ) -> QuickReply:
        buttons = []
        for action, label_key, params in items:
            label = t(label_key, language)[:_LABEL_MAX]
            buttons.append(
                QuickReplyItem(
                    action=PostbackAction(
                        label=label,
                        data=postback_data(action, **params),
                        # 按下去在聊天室留一句話，長輩看得到自己選了什麼。
                        displayText=label,
                    )
                )
            )
        return QuickReply(items=buttons)

    async def _reply(
        self,
        reply_token: str,
        user_id: str,
        text: str,
        *,
        quick_reply: Optional[QuickReply] = None,
    ) -> None:
        message = TextMessage(text=text, quickReply=quick_reply)
        await self._replier.reply_messages(reply_token, user_id, [message])
