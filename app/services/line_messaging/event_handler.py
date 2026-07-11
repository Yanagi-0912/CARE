import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
    LocationMessageContent,
    ImageMessageContent,
    VideoMessageContent,
    AudioMessageContent,
    FileMessageContent,
)
from linebot.v3.messaging import (
    ReplyMessageRequest,
    TextMessage,
    QuickReply,
    QuickReplyItem,
    LocationAction,
    ApiClient,
    Configuration,
    MessagingApi,
)
from app.services.history.history_service import LineMessageHistoryService
from app.services.media.mutimedia_processor import media_processor_service
from app.services.line_messaging.loading_animation import LineLoadingAnimationService
from app.services.line_messaging.token_manager import LineTokenManager

logger = logging.getLogger(__name__)

IMAGE_FILE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".svg",
}
VIDEO_FILE_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
AUDIO_FILE_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac"}


class LineValidationError(Exception):
    """LINE 訊息欄位格式或內容驗證失敗。"""


class LineEventHandler:

    def __init__(
        self,
        agent,
        token_manager: LineTokenManager,
        history_service: LineMessageHistoryService,
        loading_animation_service: LineLoadingAnimationService,
    ):
        self._agent = agent
        self._token_manager = token_manager
        self._history_service = history_service
        self._loading_animation_service = loading_animation_service

    async def handle(self, event: MessageEvent) -> None:
        user_id = getattr(event.source, "user_id", "")
        reply_token = getattr(event, "reply_token", "")
        if not user_id or not reply_token:
            logger.warning("LINE event source missing user_id or reply_token")
            return

        async def send_reply(
            reply_token_str: str, message_text: str, uid: str, request_location: bool = False
        ) -> bool:
            try:
                if not reply_token_str or not reply_token_str.strip():
                    raise ValueError("LINE 事件缺少 reply_token")
                if not uid or not uid.strip():
                    raise ValueError("LINE 事件缺少 user_id")

                access_token = self._token_manager.get_token()

                # 防禦性確保訊息文字為字串
                if not isinstance(message_text, str):
                    logger.warning(
                        f"send_reply received non-string message_text: {type(message_text)}. Converting to string."
                    )
                    if isinstance(message_text, list):
                        message_text = "".join(
                            part if isinstance(part, str) else (part.get("text", "") if isinstance(part, dict) else str(part))
                            for part in message_text
                        )
                    elif message_text is None:
                        message_text = ""
                    else:
                        message_text = str(message_text)

                if request_location:
                    quick_reply = QuickReply(
                        items=[
                            QuickReplyItem(
                                action=LocationAction(label="分享位置資訊")
                            )
                        ]
                    )
                    text_message = TextMessage(text=message_text, quickReply=quick_reply)
                else:
                    text_message = TextMessage(text=message_text)

                line_config = Configuration(access_token=access_token)
                with ApiClient(line_config) as api_client:
                    line_bot_api = MessagingApi(api_client)
                    line_bot_api.reply_message(
                        ReplyMessageRequest(
                            replyToken=reply_token_str,
                            messages=[text_message],
                        )
                    )

                logger.info(f"Message sent to LINE for user {uid}")
                return True

            except Exception as ex:
                logger.error(f"Failed to send LINE message: {ex}", exc_info=True)
                return False

        # 1. 處理 webhook parser 轉換後的資料，將他轉成給 agent 的格式
        try:
            message = event.message
            user_text = ""
            message_type = ""

            if isinstance(message, TextMessageContent):
                user_text, message_type = message.text, "text"

            elif isinstance(message, LocationMessageContent):
                user_text = f"這是我的目前位置：lat={message.latitude}, lng={message.longitude}"
                message_type = "location"

            elif isinstance(
                message,
                (ImageMessageContent, VideoMessageContent, AudioMessageContent, FileMessageContent),
            ):
                media_id = message.id
                media_type = message.type
                file_name = getattr(message, "file_name", None)

                # 推斷媒體類型
                if media_type == "file" and file_name:
                    extension = Path(file_name).suffix.lower()
                    if extension in IMAGE_FILE_EXTENSIONS:
                        media_type = "image"
                    elif extension in VIDEO_FILE_EXTENSIONS:
                        media_type = "video"
                    elif extension in AUDIO_FILE_EXTENSIONS:
                        media_type = "audio"
                    else:
                        media_type = "file"

                # 驗證媒體欄位
                if not media_id or not media_id.strip():
                    raise LineValidationError("缺少 media message id")
                if media_type not in {"image", "video", "audio", "file"}:
                    raise LineValidationError(f"不支援的媒體類型: {media_type}")
                if file_name is not None and not file_name.strip():
                    raise LineValidationError("無效的媒體檔名")

                media_content = await media_processor_service.process_media(
                    media_message_id=media_id,
                    user_media_type=media_type,
                    source_file_name=file_name,
                    user_id=user_id,
                )

                cleaned_content = media_content.strip()
                if (
                    not cleaned_content
                    or cleaned_content.startswith("Unable to extract text")
                    or cleaned_content.startswith("發生錯誤")
                ):
                    raise LineValidationError(f"無法從您傳送的{media_type}中辨識出任何文字，請確認內容清晰並重新傳送。")

                user_text = f"以下為使用者傳送的{media_type}媒體內容：\n{media_content}"
                message_type = media_type

            else:
                logger.warning(f"Unsupported message type: {type(message).__name__}")
                return

            event_time = datetime.fromtimestamp(event.timestamp / 1000, tz=timezone.utc)

        except LineValidationError as e:
            await send_reply(reply_token, str(e), user_id)
            return

        # 呼叫 Agent 進行思考與決策
        try:
            logger.info(f"Processing message from user {user_id}: {user_text[:50]}...")

            # 載入歷史紀錄並轉成 LangChain 訊息格式
            chat_history = await self._history_service.load_history(
                user_id=user_id,
                current_input=user_text,
                message_type=message_type,
            )

            # 先顯示 Loading，再呼叫 Agent（不論是否會用到 tool）
            await self._loading_animation_service.start(user_id)

            agent_response = await self._agent.invoke(
                user_input=user_text,
                messages=chat_history,
            )

            response_text = (
                agent_response.get("response") or "抱歉，我無法理解您的問題，請重新輸入。"
            )
            call_request_location = agent_response.get("call_request_location", False)

            # 回傳訊息
            success = await send_reply(
                reply_token, response_text, user_id, request_location=call_request_location
            )

            # 回覆成功後，儲存這一輪對話到 Redis
            if success:
                await self._history_service.save_turn(
                    user_id=user_id,
                    user_text=user_text,
                    ai_reply=response_text,
                    message_type=message_type,
                    event_time=event_time,
                )
            logger.info(f"Successfully processed and replied to user {user_id}")

        except Exception as e:
            logger.error(f"Error in processing Line message event: {e}", exc_info=True)
            await send_reply(
                reply_token, "抱歉，處理您的訊息時發生錯誤，請稍後再試", user_id
            )
