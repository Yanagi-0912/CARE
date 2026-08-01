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
from app.core.request_logging import log_done, log_start
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
            await self._replier.reply(
                reply_token=reply_token,
                message_text=str(e),
                user_id=user_id,
                voice_reply_enabled=False,
            )
        except Exception:
            status = "error"
            logger.exception("Error in event dispatcher handling event %s", event_type)
            await self._replier.reply(
                reply_token=reply_token,
                message_text="抱歉，處理您的事件時發生錯誤，請稍後再試",
                user_id=user_id,
                voice_reply_enabled=False,
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
        reply_token = getattr(event, "reply_token", "")
        postback_data = getattr(getattr(event, "postback", None), "data", "")

        params = parse_qs(postback_data)
        action = params.get("action", [""])[0]

        if action == "confirm_medication":
            log_id = params.get("log_id", [""])[0]
            if not log_id:
                logger.warning("confirm_medication postback missing log_id")
                return

            if self._medication_service:
                log = await self._medication_service.confirm_medication(log_id, user_id)
                taken_time_str = log.taken_at.strftime("%H:%M") if log.taken_at else ""
                scheduled_time_str = (
                    log.scheduled_at.strftime("%H:%M") if log.scheduled_at else "08:00"
                )

                disabled_flex = build_patient_medication_flex(
                    log_id=log_id,
                    slot_type=log.slot_type,
                    scheduled_time=scheduled_time_str,
                    disabled=True,
                    taken_at_str=taken_time_str,
                )
                await self._replier.reply_flex(
                    reply_token=reply_token,
                    flex_message=disabled_flex,
                    user_id=user_id,
                )
            else:
                await self._replier.reply(
                    reply_token=reply_token,
                    message_text="已記錄您的服藥狀態！",
                    user_id=user_id,
                    voice_reply_enabled=False,
                )
        elif action == "already_done":
            await self._replier.reply(
                reply_token=reply_token,
                message_text="此服藥提醒先前已完成紀錄囉！祝您身體健康！",
                user_id=user_id,
                voice_reply_enabled=False,
            )
        elif action == "toggle_voice_reply":
            enabled_str = params.get("enabled", ["false"])[0]
            enabled = enabled_str.lower() == "true"
            if self._user_profile_service:
                await self._user_profile_service.update_voice_reply_enabled(user_id, enabled)

            status_msg = "已開啟語音回覆" if enabled else "已關閉語音回覆"
            await self._replier.reply(
                reply_token=reply_token,
                message_text=status_msg,
                user_id=user_id,
                voice_reply_enabled=False,
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
