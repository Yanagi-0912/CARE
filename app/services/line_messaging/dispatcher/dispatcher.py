import logging
import time
from urllib.parse import parse_qs

from linebot.v3.webhooks import (
    AudioMessageContent,
    FileMessageContent,
    ImageMessageContent,
    LocationMessageContent,
    MessageEvent,
    PostbackEvent,
    TextMessageContent,
    VideoMessageContent,
)
from app.core.request_context import (
    new_request_id,
    reset_request_id,
    set_request_id,
)
from app.core.user_font_size import (
    normalize_user_font_size,
    reset_request_font_size,
    set_request_font_size,
)
from app.core.user_language import (
    DEFAULT_USER_LANGUAGE,
    normalize_user_language,
    reset_request_language,
    set_request_language,
)
from app.core.request_logging import log_done, log_start
from app.i18n.messages import t
from app.models.medication import to_taipei_hm
from app.services.line_messaging.flex.medication_flex import build_patient_medication_flex
from app.services.line_messaging.handler.message_handler import (
    LineMessageHandler,
    LineValidationError,
)
from app.services.line_messaging.handler.facility_detail_handler import LineFacilityDetailHandler
from app.services.line_messaging.handler.media_handler import LineMediaHandler
from app.services.line_messaging.handler.location_handler import LineLocationHandler
from app.services.line_messaging.reply.reply import LineReplier

logger = logging.getLogger(__name__)


def _event_label(event) -> str:
    if isinstance(event, PostbackEvent):
        return "postback"
    if isinstance(event, MessageEvent):
        message = event.message
        if isinstance(message, TextMessageContent):
            return "text"
        if isinstance(message, LocationMessageContent):
            return "location"
        if isinstance(
            message,
            (
                ImageMessageContent,
                VideoMessageContent,
                AudioMessageContent,
                FileMessageContent,
            ),
        ):
            return getattr(message, "type", None) or type(message).__name__
        return f"message:{type(message).__name__}"
    return type(event).__name__


class LineEventDispatcher:
    """事件分發器，負責接收 Webhook 解析的事件，並分發至對應處理器。"""

    def __init__(
        self,
        message_handler: LineMessageHandler,
        media_handler: LineMediaHandler,
        location_handler: LineLocationHandler,
        facility_detail_handler: LineFacilityDetailHandler,
        replier: LineReplier,
        medication_service=None,
    ):
        self._message_handler = message_handler
        self._media_handler = media_handler
        self._location_handler = location_handler
        self._facility_detail_handler = facility_detail_handler
        self._replier = replier
        self._medication_service = medication_service


    async def handle(self, event: MessageEvent) -> None:
        """分發單一事件至對應的方法處理。"""
        user_id = getattr(event.source, "user_id", "")
        reply_token = getattr(event, "reply_token", "")
        if not user_id or not reply_token:
            logger.warning("LINE event source missing user_id or reply_token")
            return

        rid_token = set_request_id(new_request_id())
        started = time.perf_counter()
        status = "ok"
        event_type = type(event).__name__
        handler = getattr(self, f"_handle_{event_type}", self._handle_unsupported_event)

        try:
            log_start(
                logger,
                event=_event_label(event),
                user=user_id[:10],
            )
            await handler(event)
        except LineValidationError as e:
            status = "validation_error"
            user_language = await self._resolve_user_language(user_id)
            await self._replier.reply(
                reply_token=reply_token,
                message_text=str(e),
                user_id=user_id,
                voice_reply_enabled=False,
                language=user_language,
            )
        except Exception:
            status = "error"
            logger.exception("Error in event dispatcher handling event %s", event_type)
            user_language = await self._resolve_user_language(user_id)
            await self._replier.reply(
                reply_token=reply_token,
                message_text=t("line.fallback_process_error", language=user_language),
                user_id=user_id,
                voice_reply_enabled=False,
                language=user_language,
            )
        finally:
            total_ms = int((time.perf_counter() - started) * 1000)
            log_done(logger, status=status, total_ms=total_ms)
            reset_request_id(rid_token)

    async def _handle_MessageEvent(self, event: MessageEvent) -> None:
        message = event.message

        if isinstance(message, TextMessageContent):
            await self._message_handler.handle(event)
        elif isinstance(message, LocationMessageContent):
            await self._location_handler.handle(event)
        elif isinstance(
            message,
            (
                ImageMessageContent,
                VideoMessageContent,
                AudioMessageContent,
                FileMessageContent,
            ),
        ):
            await self._media_handler.handle(event)
        else:
            logger.warning("Unsupported message content type: %s", type(message).__name__)

    async def _handle_PostbackEvent(self, event: PostbackEvent) -> None:
        user_id = getattr(event.source, "user_id", "")
        user_profile = None
        if self._user_profile_service:
            user_profile = await self._user_profile_service.get_user_profile(user_id)

        # 下游 handler 只拿得到 user_id，語言與字級改由 ContextVar 傳遞
        lang_token = set_request_language(self._language_from_profile(user_profile))
        font_token = set_request_font_size(self._font_size_from_profile(user_profile))
        try:
            await self._dispatch_postback(event, user_id, user_profile)
        finally:
            reset_request_language(lang_token)
            reset_request_font_size(font_token)

    async def _dispatch_postback(
        self, event: PostbackEvent, user_id: str, user_profile
    ) -> None:
        reply_token = getattr(event, "reply_token", "")
        postback_data = getattr(getattr(event, "postback", None), "data", "")
        user_language = self._language_from_profile(user_profile)

        params = parse_qs(postback_data)
        action = params.get("action", [""])[0]

        if action == "confirm_medication":
            log_id = params.get("log_id", [""])[0]
            if not log_id:
                logger.warning("confirm_medication postback missing log_id")
                return

            if self._medication_service:
                log = await self._medication_service.confirm_medication(log_id, user_id)
                taken_time_str = to_taipei_hm(log.taken_at)
                scheduled_time_str = to_taipei_hm(log.scheduled_at, default="08:00")
                # 已完成的卡片要留下「這次吃了哪幾種藥」——提醒卡上有的資訊
                # 不該在按下確認後就消失，那是使用者事後唯一查得到的憑據。
                # 查不到時回傳空清單，卡片自動退回沒有藥品區塊的原樣。
                medication_names = (
                    await self._medication_service.list_medication_names_for_log(log)
                )

                disabled_flex = build_patient_medication_flex(
                    log_id=log_id,
                    slot_type=log.slot_type,
                    scheduled_time=scheduled_time_str,
                    disabled=True,
                    taken_at_str=taken_time_str,
                    medication_names=medication_names,
                    language=user_language,
                    font_size=self._font_size_from_profile(user_profile),
                )
                await self._replier.reply_flex(
                    reply_token=reply_token,
                    flex_message=disabled_flex,
                    user_id=user_id,
                )
            else:
                await self._replier.reply(
                    reply_token=reply_token,
                    message_text=t("meds.recorded", language=user_language),
                    user_id=user_id,
                    voice_reply_enabled=False,
                    language=user_language,
                )
        elif action == "already_done":
            await self._replier.reply(
                reply_token=reply_token,
                message_text=t("meds.already_recorded", language=user_language),
                user_id=user_id,
                voice_reply_enabled=False,
                language=user_language,
            )
        elif action == "toggle_voice_reply":
            if "enabled" in params:
                enabled = params.get("enabled", ["false"])[0].lower() == "true"
            else:
                current = self._parse_voice_reply_enabled(user_profile)
                enabled = not current
            updated = False
            if self._user_profile_service:
                updated = await self._user_profile_service.update_voice_reply_enabled(
                    user_id, enabled
                )

            if updated:
                status_msg = (
                    t("voice.enabled", language=user_language)
                    if enabled
                    else t("voice.disabled", language=user_language)
                )
            else:
                status_msg = t("voice.need_login", language=user_language)
            await self._replier.reply(
                reply_token=reply_token,
                message_text=status_msg,
                user_id=user_id,
                voice_reply_enabled=False,
                language=user_language,
            )
        #點擊"查看院所詳細資訊"按鈕時，回應該診所的詳細資料
        elif action == "view_facility_detail":
            facility_id = params.get("facility_id", [""])[0]
            await self._facility_detail_handler.handle_view_facility_detail(
                facility_id=facility_id,
                reply_token=reply_token,
                user_id=user_id,
            )
        else:
            logger.warning("Unknown postback action: %s", action)


    async def _handle_unsupported_event(self, event) -> None:
        logger.warning("Unsupported LINE event type: %s", type(event).__name__)

    async def _resolve_user_language(self, user_id: str) -> str:
        if not self._user_profile_service:
            return DEFAULT_USER_LANGUAGE
        profile = await self._user_profile_service.get_user_profile(user_id)
        return self._language_from_profile(profile)

    @staticmethod
    def _language_from_profile(user_profile) -> str:
        if not user_profile:
            return DEFAULT_USER_LANGUAGE
        settings = user_profile.get("settings") or {}
        return normalize_user_language(settings.get("language"))

    @staticmethod
    def _font_size_from_profile(user_profile) -> str:
        settings = (user_profile or {}).get("settings") or {}
        return normalize_user_font_size(settings.get("font_size"))

    @staticmethod
    def _parse_voice_reply_enabled(user_profile) -> bool:
        """解析 profile 的語音回覆開關；缺省為 False（與 UserSettings 一致）。"""
        if not user_profile:
            return False
        settings = user_profile.get("settings") or {}
        if isinstance(settings, dict) and "voice_reply_enabled" in settings:
            return bool(settings["voice_reply_enabled"])
        return bool(user_profile.get("voice_reply_enabled", False))

    # --- 以下屬性為回溯相容與測試相容所設 ---

    @property
    def _agent(self):
        return self._message_handler._agent

    @property
    def _token_manager(self):
        return self._replier._token_manager

    @property
    def _user_profile_service(self):
        return self._message_handler._user_profile_service

    @property
    def _history_service(self):
        return self._message_handler._history_service

    @property
    def _tts_service(self):
        return self._replier._tts_service
