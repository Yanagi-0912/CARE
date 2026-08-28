import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from linebot.v3.webhooks import MessageEvent, TextMessageContent

from app.core.request_logging import log_stage
from app.core.user_font_size import (
    normalize_user_font_size,
    reset_request_font_size,
    set_request_font_size,
)
from app.core.user_age import reset_request_age, set_request_age
from app.core.user_language import (
    DEFAULT_USER_LANGUAGE,
    normalize_user_language,
    reset_request_language,
    set_request_language,
)
from app.i18n.messages import t
from app.services.line_messaging.reply.reply import LineReplier
from app.core.request_context import reset_line_user_id, set_line_user_id

logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[Handler:MessageHandler]"


class LineValidationError(Exception):
    """LINE 訊息欄位格式或內容驗證失敗。"""


class BaseLineMessageHandler:
    """LINE 訊息處理器的基底類別，處理共通的 Agent 呼叫、歷史紀錄與 Reply 調用邏輯。"""

    def __init__(
        self,
        agent,
        history_service,
        user_profile_service,
        replier: LineReplier,
        loading_animation_service=None,
        safety_alert_service=None,
    ):
        self._agent = agent
        self._history_service = history_service
        self._user_profile_service = user_profile_service
        self._replier = replier
        self._loading_animation_service = loading_animation_service
        # 功能關閉時 dependencies 根本不會組出這個服務，這裡就是 None，
        # 整條路徑一步都不會執行（見 app/dependencies.py 的組裝）。
        self._safety_alert_service = safety_alert_service
        # 併行任務要被持有參考直到完成，否則可能在跑完之前就被 GC 回收。
        self._safety_alert_tasks: set[asyncio.Task] = set()

    async def _process_and_reply(
        self,
        event: MessageEvent,
        user_text: str,
        message_type: str,
    ) -> None:
        user_id = getattr(event.source, "user_id", "")
        reply_token = getattr(event, "reply_token", "")
        event_time = datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)

        user_language = DEFAULT_USER_LANGUAGE
        lang_token = None
        font_token = None

        try:
            log_stage(
                logger,
                "handle",
                type=message_type,
                text_len=len(user_text or ""),
            )

            # 用藥風險評估與主回覆併行。放在這裡是因為此處是文字與媒體訊息的
            # 共用匯流點，且媒體的 OCR 文字已經備妥——判定所需的訊號全在字串
            # 裡，不需要再碰一次影像。它不參與下面任何一步，主回覆的內容與
            # 時序完全不受影響。
            self._schedule_safety_alert_check(user_id, user_text)

            t0 = time.perf_counter()
            chat_history = await self._history_service.load_history(
                user_id=user_id,
                current_input=user_text,
                message_type=message_type,
            )
            log_stage(
                logger,
                "history_loaded",
                turns=len(chat_history or []),
                ms=int((time.perf_counter() - t0) * 1000),
            )

            user_profile = None
            if self._user_profile_service:
                user_profile = await self._user_profile_service.get_user_profile(
                    user_id
                )

            user_language = self._language_from_profile(user_profile)
            lang_token = set_request_language(user_language)
            # Agent 產生的 Flex Message 走 LangChain tool，拿不到 user_profile，
            # 因此字級也比照語言存進 request-scoped ContextVar
            font_token = set_request_font_size(
                self._font_size_from_profile(user_profile)
            )
            # 年齡同理：症狀科別建議要靠它決定該不該給兒科，而那段程式在
            # LangChain tool 底下，拿不到 user_profile。
            age_token = set_request_age((user_profile or {}).get("age"))

            if self._loading_animation_service is not None:
                await self._loading_animation_service.start(user_id)

            t1 = time.perf_counter()
            line_user_token = set_line_user_id(user_id)
            try:
                agent_response = await self._agent.invoke(
                    user_input=user_text,
                    messages=chat_history,
                    user_profile=user_profile,
                )
            finally:
                reset_line_user_id(line_user_token)
            log_stage(
                logger,
                "agent_done",
                ms=int((time.perf_counter() - t1) * 1000),
            )

            response_payload = agent_response.get("response")
            if not response_payload:
                logger.warning(
                    f"{LOGGER_HEADER_TEXT} agent 回覆為空，將使用預設純文字回覆，message_type=%s, user_id=%s",
                    message_type,
                    user_id,
                )
            else:
                logger.info(
                    f"{LOGGER_HEADER_TEXT} 已取得 agent 回覆，message_type=%s, response_type=%s",
                    message_type,
                    type(response_payload).__name__,
                )

            response_text = response_payload or t(
                "line.fallback_ununderstood", language=user_language
            )
            call_request_location = agent_response.get("call_request_location", False)
            voice_reply_enabled = self._parse_voice_reply_enabled(user_profile)
            voice_rate = self._parse_voice_rate(user_profile)
            voice_gender = self._parse_voice_gender(user_profile)

            t2 = time.perf_counter()
            success = await self._replier.reply(
                reply_token=reply_token,
                message_text=response_text,
                user_id=user_id,
                request_location=call_request_location,
                voice_reply_enabled=voice_reply_enabled,
                language=user_language,
                voice_rate=voice_rate,
                voice_gender=voice_gender,
            )
            log_stage(
                logger,
                "reply",
                ok=success,
                ms=int((time.perf_counter() - t2) * 1000),
            )

            if success:
                await self._history_service.save_turn(
                    user_id=user_id,
                    user_text=user_text,
                    ai_reply=response_text,
                    message_type=message_type,
                    event_time=event_time,
                )
            else:
                logger.error("Failed to reply to user %s", user_id)

        except Exception:
            logger.exception("Error in processing Line message event")
            await self._replier.reply(
                reply_token=reply_token,
                message_text=t("line.fallback_process_error", language=user_language),
                user_id=user_id,
                voice_reply_enabled=False,
                language=user_language,
            )
        finally:
            if lang_token is not None:
                reset_request_language(lang_token)
            if font_token is not None:
                reset_request_font_size(font_token)
                reset_request_age(age_token)

    def _schedule_safety_alert_check(self, user_id: str, user_text: str) -> None:
        """把一次風險評估丟到背景執行。

        失敗一律留在任務內部：使用者沒有在等這個結果，讓例外逸散只會變成
        "Task exception was never retrieved"，而且會污染主流程的錯誤處理。
        """
        if self._safety_alert_service is None or not user_id or not user_text:
            return

        async def _run() -> None:
            try:
                await self._safety_alert_service.check(user_id, user_text)
            except Exception:  # noqa: BLE001 - 背景旁路，例外不得逸散
                logger.exception("用藥風險評估任務失敗")

        task = asyncio.create_task(_run())
        self._safety_alert_tasks.add(task)
        task.add_done_callback(self._safety_alert_tasks.discard)

    @staticmethod
    def _language_from_profile(user_profile: Optional[dict]) -> str:
        if not user_profile:
            return DEFAULT_USER_LANGUAGE
        settings = user_profile.get("settings") or {}
        return normalize_user_language(settings.get("language"))

    @staticmethod
    def _font_size_from_profile(user_profile: Optional[dict]) -> str:
        settings = (user_profile or {}).get("settings") or {}
        return normalize_user_font_size(settings.get("font_size"))

    def _parse_voice_reply_enabled(self, user_profile: Optional[dict]) -> bool:
        """同步解析使用者個人檔案中的語音回覆設定；缺省為 False（與 UserSettings 及 LIFF UI 一致）。"""
        if not user_profile:
            return False
        settings_dict = user_profile.get("settings") or {}
        if "voice_reply_enabled" in settings_dict:
            return bool(settings_dict["voice_reply_enabled"])
        return bool(user_profile.get("voice_reply_enabled", False))

    def _parse_voice_rate(self, user_profile: Optional[dict]) -> str:
        """同步解析使用者個人檔案中的語速設定，缺值時預設為 "normal"。"""
        if not user_profile:
            return "normal"
        settings_dict = user_profile.get("settings") or {}
        if "voice_rate" in settings_dict:
            return settings_dict["voice_rate"] or "normal"
        return user_profile.get("voice_rate") or "normal"

    def _parse_voice_gender(self, user_profile: Optional[dict]) -> str:
        """同步解析使用者個人檔案中的語音性別設定，缺值時預設為 "female"。"""
        if not user_profile:
            return "female"
        settings_dict = user_profile.get("settings") or {}
        if "voice_gender" in settings_dict:
            return settings_dict["voice_gender"] or "female"
        return user_profile.get("voice_gender") or "female"


class LineMessageHandler(BaseLineMessageHandler):
    """處理文字訊息事件。"""

    async def handle(self, event: MessageEvent) -> None:
        message = event.message
        if not isinstance(message, TextMessageContent):
            raise ValueError("Expected TextMessageContent")
        await self._process_and_reply(event, message.text, "text")
