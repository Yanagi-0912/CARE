from pathlib import Path
from typing import Optional, cast
from linebot.v3.webhooks import (
    MessageEvent,
    PostbackEvent,
    TextMessageContent,
    LocationMessageContent,
    ImageMessageContent,
    VideoMessageContent,
    AudioMessageContent,
    FileMessageContent,
)

from app.services.media.mutimedia_processor import media_processor_service
from app.services.line_messaging.shared.validation import (
    validate_media_message,
    validate_reply_context,
    validate_text_message,
)
import logging

logger = logging.getLogger(__name__)

IMAGE_FILE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".bmp",
    ".tiff",
    ".webp",
    ".svg",
}
VIDEO_FILE_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
AUDIO_FILE_EXTENSIONS = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".opus", ".aac"}


class LineEventHandler:
    """處理 webhook 接受到的 event 並分發到對應的 service"""

    def __init__(
        self,
        agent,
        line_message_service,
        user_profile_service,
    ):
        self._agent = agent
        self._line_message_service = line_message_service
        self._user_profile_service = user_profile_service

    async def handle(self, event) -> None:
        """對外進入點"""
        user_id = getattr(event.source, "user_id", None)
        
        # MessageEvent 才需要驗證 reply_token，PostbackEvent 也有但用途不同
        if isinstance(event, MessageEvent):
            validate_reply_context(event.reply_token, user_id)
            reply_token = event.reply_token
            message = event.message

            if isinstance(message, TextMessageContent):
                await self._handle_text_message(message, reply_token, user_id)
            elif isinstance(message, LocationMessageContent):
                await self._handle_location_message(message, reply_token, user_id)
            elif isinstance(
                message,
                (
                    ImageMessageContent,
                    VideoMessageContent,
                    AudioMessageContent,
                    FileMessageContent,
                ),
            ):
                await self._handle_media_message(message, reply_token, user_id)
            else:
                logger.warning(f"Unsupported message type: {type(message).__name__}")
        
        elif isinstance(event, PostbackEvent):
            await self._handle_postback_event(event, user_id)
        
        else:
            logger.warning(f"Unsupported event type: {type(event).__name__}")

    async def _handle_text_message(
        self, message: TextMessageContent, reply_token: str, user_id: Optional[str]
    ) -> None:
        """處理文字訊息"""
        logger.info(f"Received text message event from user {user_id}")
        user_text = validate_text_message(message.text)

        await self._invoke_and_reply(
            user_text=user_text,
            reply_token=reply_token,
            user_id=user_id,
        )

    async def _handle_location_message(
        self, message: LocationMessageContent, reply_token: str, user_id: Optional[str]
    ) -> None:
        """處理位置訊息"""
        lat: float = message.latitude
        lng: float = message.longitude

        logger.info(f"Received location from user {user_id}: ({lat}, {lng})")

        # 將位置資訊轉為純文字，讓 LangGraph Agent 決定如何使用 (例如觸發 find_nearby_hospitals)
        location_text = f"這是我的目前位置：lat={lat}, lng={lng}"

        await self._invoke_and_reply(
            user_text=location_text,
            reply_token=reply_token,
            user_id=user_id,
        )

    async def _handle_media_message(
        self, message, reply_token: str, user_id: Optional[str]
    ) -> None:
        """處理多媒體訊息"""
        media_id = message.id
        media_type = message.type
        file_name = getattr(message, "file_name", None)

        if media_type == "file" and file_name:
            media_type = self._infer_media_type_from_file_name(file_name)
        validate_media_message(media_id, media_type, file_name)

        log_msg = f"Received {media_type} message event from user {user_id}"
        if file_name:
            log_msg += f": {file_name}"
        logger.info(log_msg)

        media_content = await media_processor_service.process_media(
            media_message_id=media_id,
            user_media_type=media_type,
            source_file_name=file_name,
            user_id=user_id,
        )

        await self._invoke_and_reply(
            user_text=f"以下為用戶傳送的{media_type}媒體內容：\n{media_content}",
            reply_token=reply_token,
            user_id=user_id,
        )

    @staticmethod
    def _infer_media_type_from_file_name(file_name: str) -> str:
        """根據副檔名推斷媒體類型"""
        extension = Path(file_name).suffix.lower()
        if extension in IMAGE_FILE_EXTENSIONS:
            return "image"
        if extension in VIDEO_FILE_EXTENSIONS:
            return "video"
        if extension in AUDIO_FILE_EXTENSIONS:
            return "audio"
        return "file"

    async def _handle_postback_event(self, event: PostbackEvent, user_id: Optional[str]) -> None:
        """處理 postback 事件（例如 rich menu 切換指令）"""
        try:
            postback_data = event.postback.data
            reply_token = event.reply_token
            
            logger.info(f"Received postback event from user {user_id}: {postback_data}")
            
            # 解析 postback data，格式例如：action=toggle_voice_reply&enabled=true
            params = {}
            for param in postback_data.split("&"):
                if "=" in param:
                    key, value = param.split("=", 1)
                    params[key] = value
            
            action = params.get("action")
            
            if action == "toggle_voice_reply":
                # 切換語音回覆設定
                enabled_str = params.get("enabled", "true").lower()
                enabled = enabled_str == "true"
                
                # 取得使用者當前資料
                profile = await self._user_profile_service.get_user_profile(user_id)
                if profile is None:
                    logger.warning(f"User profile not found for {user_id}")
                    response_text = "抱歉，無法找到您的使用者資料。請稍後再試。"
                else:
                    # 更新使用者偏好
                    profile["voice_reply_enabled"] = enabled
                    await self._user_profile_service.upsert_user_profile(user_id, profile)
                    
                    status_text = "已開啟" if enabled else "已關閉"
                    response_text = f"✓ 語音回覆{status_text}成功"
                    logger.info(f"User {user_id} toggled voice_reply_enabled to {enabled}")
                
                # 回覆確認訊息
                await self._line_message_service.send_line_reply(
                    reply_token,
                    response_text,
                    user_id,
                    voice_reply_enabled=False,
                )
            else:
                logger.warning(f"Unknown postback action: {action}")
                await self._line_message_service.send_line_reply(
                    reply_token,
                    "抱歉，無法識別該操作。",
                    user_id,
                    voice_reply_enabled=False,
                )
        
        except Exception as e:
            logger.error(f"Error handling postback event: {e}", exc_info=True)
            reply_token = getattr(event, "reply_token", None)
            if reply_token:
                await self._line_message_service.send_line_reply(
                    reply_token,
                    "處理您的操作時發生錯誤，請稍後再試。",
                    user_id,
                    voice_reply_enabled=False,
                )

    async def _invoke_and_reply(
        self, user_text: str, reply_token: str, user_id: Optional[str] = None
    ) -> bool:
        """呼叫 Agent 並回覆訊息"""
        try:
            logger.info(f"Processing message from user {user_id}: {user_text[:50]}...")

            # 呼叫 Agent 進行決策
            result = await self._agent.invoke(user_input=user_text)

            # 回傳 Agent 最終產出的文字回覆
            response_text = (
                result.get("response") or "抱歉，我無法理解您的問題，請重新輸入。"
            )
            call_request_location = result.get("call_request_location", False)
            
            # 讀取使用者的語音回覆偏好
            voice_reply_enabled = True
            try:
                profile = await self._user_profile_service.get_user_profile(user_id)
                if profile:
                    voice_reply_enabled = profile.get("voice_reply_enabled", True)
            except Exception as e:
                logger.warning(f"Failed to fetch user voice preference: {e}")
            
            kwargs = {}
            if call_request_location:
                kwargs["request_location"] = True
            if not voice_reply_enabled:
                # 若語音回覆未啟用，可在此加入標記（未來會用於決定是否生成音訊回覆）
                kwargs["voice_reply_enabled"] = False
            
            success = await self._line_message_service.send_line_reply(
                reply_token, response_text, user_id, **kwargs
            )

            if success:
                logger.info(f"Successfully processed and replied to user {user_id}")
            return success

        except Exception as e:
            logger.error(f"Error in processing message: {e}", exc_info=True)
            error_message = "抱歉，處理您的訊息時發生錯誤，請稍後再試"
            await self._line_message_service.send_line_reply(
                reply_token, error_message, user_id
            )
            return False
