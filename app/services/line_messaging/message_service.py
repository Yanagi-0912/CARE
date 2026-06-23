from typing import Optional, Protocol

from linebot.v3.messaging import (
    AudioMessage,
    Message,
    ReplyMessageRequest,
    TextMessage,
    QuickReply,
    QuickReplyItem,
    LocationAction,
)

from app.core.config import settings
from app.services.line_messaging.shared.errors import LineTokenError, LineValidationError
from app.services.line_messaging.shared.validation import (
    validate_reply_context,
)
import logging
from pathlib import Path
from urllib.parse import quote

logger = logging.getLogger(__name__)


class TokenProvider(
    Protocol
):  # protocol 是來規範 傳進來的 di 要怎麼使用 建立一個role 給他
    def get_token(self) -> str: ...


"""get_token() 的實作在 LineTokenManager；
LineMessageService 只依賴 TokenProvider 介面，
具體實例由 dependencies.py 建立並注入，避免 service 耦合具體類別與建立邏輯。
"""


class MedicalServiceLike(Protocol):
    pass


class LineMessagingClientLike(Protocol):
    def reply_message(
        self, access_token: str, request: ReplyMessageRequest
    ) -> None: ...


class LineMessageService:
    def __init__(  # 依賴注入點
        # 有沒有想過不直接使用 token_manager 的class 就好
        # 因為要 low coupling 不要使用 直接依賴 token_manager 的class 會造成耦合
        self,
        token_provider: TokenProvider,
        medical_service: MedicalServiceLike,
        line_messaging_client: LineMessagingClientLike,
        tts_service=None,
    ):
        self.token_provider = token_provider
        self.medical_service = medical_service
        self.line_messaging_client = line_messaging_client
        self.tts_service = tts_service
        logger.info("LineMessageService initialized with Gemini AI and TTS")
        if self.tts_service is None:
            logger.info("No TTSService provided; voice replies will be disabled.")
        else:
            logger.info("TTSService available for voice replies.")
            if hasattr(self.tts_service, "available") and not self.tts_service.available():
                logger.warning("TTSService instance present but reports unavailable; voice replies may fail.")

    async def send_line_reply(
        self,
        reply_token: str,
        message_text: str,
        user_id: Optional[str] = None,
        request_location: bool = False,
        voice_reply_enabled: bool = True,
        tts_locale: str = "zh-TW",
    ) -> bool:
        try:
            validate_reply_context(reply_token, user_id)
            access_token = self.token_provider.get_token()
            
            # 註：voice_reply_enabled 參數目前保留用於未來的 TTS 集成，暫時不影響當前的文字回覆
            # Defensively ensure message_text is a string to avoid Pydantic validation errors for TextMessage
            if not isinstance(message_text, str):
                logger.warning(
                    f"send_line_reply received non-string message_text: {type(message_text)}. Converting to string."
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
                text_message = TextMessage(text=message_text, quick_reply=quick_reply)
            else:
                text_message = TextMessage(text=message_text, quick_reply=None)

            messages: list[Message] = [text_message]

            # If voice reply requested and TTS service available, synthesize audio.
            logger.info(
                "Voice reply check for user %s: enabled=%s, tts_service=%s, public_base_url_set=%s",
                user_id,
                voice_reply_enabled,
                type(self.tts_service).__name__ if self.tts_service is not None else None,
                bool(settings.PUBLIC_BASE_URL.strip()),
            )
            if not voice_reply_enabled:
                logger.info(f"Voice reply disabled for user {user_id}; sending text only.")
            elif self.tts_service is None:
                logger.warning("TTS service is not configured; sending text only.")
            else:
                try:
                    if hasattr(self.tts_service, "available") and not self.tts_service.available():
                        logger.warning("TTS service reports unavailable; attempting synthesis anyway.")

                    _audio_bytes, filename, _duration_ms = self.tts_service.synthesize(
                        message_text, locale=tts_locale
                    )
                    tmp_path = Path(filename)
                    public_base_url = settings.PUBLIC_BASE_URL.rstrip("/")
                    audio_url_path = settings.TTS_AUDIO_URL_PATH.strip("/") or "tts"
                    duration_ms = int(_duration_ms or 60_000)

                    if public_base_url and tmp_path.exists():
                        audio_url = (
                            f"{public_base_url}/{audio_url_path}/"
                            f"{quote(tmp_path.name)}"
                        )
                        messages.append(
                            AudioMessage(
                                original_content_url=audio_url,
                                duration=int(duration_ms),
                            )
                        )
                        logger.info(f"TTS audio message prepared: {audio_url}")
                    elif not public_base_url:
                        logger.warning(
                            "PUBLIC_BASE_URL is not set; skipping LINE audio reply."
                        )
                    else:
                        logger.warning(f"TTS output file not found: {tmp_path}")

                except Exception:
                    logger.exception("TTS generation failed; falling back to text reply.")
            logger.info(
                "Prepared %s LINE message(s) for user %s: %s",
                len(messages),
                user_id,
                [getattr(message, "type", type(message).__name__) for message in messages],
            )
            self.line_messaging_client.reply_message(
                access_token,
                ReplyMessageRequest(
                    replyToken=reply_token,
                    messages=messages,
                ),
            )

            logger.info(f"Message sent to LINE for user {user_id}")
            return True

        except LineValidationError as e:
            logger.warning(f"Validation in send_line_reply: {e}")
            return False

        except LineTokenError as e:
            logger.error(f"Failed to get LINE token: {e}")
            return False

        except Exception as e:
            logger.error(f"Failed to send LINE message: {e}", exc_info=True)
            return False


    async def _send_error_reply(
        self, reply_token: str, user_id: Optional[str] = None
    ) -> bool:
        try:
            error_message = "抱歉，處理您的訊息時發生錯誤，請稍後再試"
            return await self.send_line_reply(reply_token, error_message, user_id)
        except Exception as e:
            logger.error(f"Failed to send error reply: {e}")
            return False
