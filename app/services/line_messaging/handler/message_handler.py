import logging
from datetime import datetime, timezone
from typing import Optional

from linebot.v3.webhooks import MessageEvent, TextMessageContent
from app.services.line_messaging.reply.reply import LineReplier

logger = logging.getLogger(__name__)


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
    ):
        self._agent = agent
        self._history_service = history_service
        self._user_profile_service = user_profile_service
        self._replier = replier
        self._loading_animation_service = loading_animation_service

    async def _process_and_reply(
        self,
        event: MessageEvent,
        user_text: str,
        message_type: str,
    ) -> None:
        user_id = getattr(event.source, "user_id", "")
        reply_token = getattr(event, "reply_token", "")
        event_time = datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)

        try:
            logger.info("Processing %s message from user %s: %s...", message_type, user_id, user_text[:50])

            chat_history = await self._history_service.load_history(
                user_id=user_id,
                current_input=user_text,
                message_type=message_type,
            )

            # 取得使用者資料
            user_profile = None
            if self._user_profile_service:
                user_profile = await self._user_profile_service.get_user_profile(user_id)

            # 先顯示 Loading，再呼叫 Agent
            if self._loading_animation_service is not None:
                await self._loading_animation_service.start(user_id)

            # 呼叫 Agent 大腦層
            agent_response = await self._agent.invoke(
                user_input=user_text,
                messages=chat_history,
                user_profile=user_profile,
            )

            response_text = (
                agent_response.get("response") or "抱歉，我無法理解您的問題，請重新輸入。"
            )
            call_request_location = agent_response.get("call_request_location", False)
            voice_reply_enabled = self._parse_voice_reply_enabled(user_profile)

            # 呼叫 Reply 層發送回覆
            success = await self._replier.reply(
                reply_token=reply_token,
                message_text=response_text,
                user_id=user_id,
                request_location=call_request_location,
                voice_reply_enabled=voice_reply_enabled,
            )

            if success:
                await self._history_service.save_turn(
                    user_id=user_id,
                    user_text=user_text,
                    ai_reply=response_text,
                    message_type=message_type,
                    event_time=event_time,
                )
            logger.info("Successfully processed and replied to user %s", user_id)

        except Exception:
            logger.exception("Error in processing Line message event")
            await self._replier.reply(
                reply_token=reply_token,
                message_text="抱歉，處理您的訊息時發生錯誤，請稍後再試",
                user_id=user_id,
                voice_reply_enabled=False,
            )

    def _parse_voice_reply_enabled(self, user_profile: Optional[dict]) -> bool:
        """同步解析使用者個人檔案中的語音回覆設定，預設為 True。"""
        if not user_profile:
            return True
        settings_dict = user_profile.get("settings") or {}
        if "voice_reply_enabled" in settings_dict:
            return bool(settings_dict["voice_reply_enabled"])
        return bool(user_profile.get("voice_reply_enabled", True))


class LineMessageHandler(BaseLineMessageHandler):
    """處理文字訊息事件。"""

    async def handle(self, event: MessageEvent) -> None:
        message = event.message
        if not isinstance(message, TextMessageContent):
            raise ValueError("Expected TextMessageContent")
        await self._process_and_reply(event, message.text, "text")
