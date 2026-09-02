"""LINE Messaging API Channel Access Token 與回覆管理。

負責向 LINE OAuth 換取 Bot 用的 access token 並作快取，以及建構回覆訊息（包含 Text、Audio/TTS）。
"""

from __future__ import annotations

import json
import logging

from app.core.request_logging import stage_timer
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote
import requests

from linebot.v3.messaging import (
    ApiClient,
    AudioMessage,
    Configuration,
    FlexContainer,
    FlexMessage,
    LocationAction,
    MessagingApi,
    PushMessageRequest,
    QuickReply,
    QuickReplyItem,
    ReplyMessageRequest,
    TextMessage,
)

from app.core.config import settings
from app.core.rag_sources import get_request_rag_sources
from app.i18n.messages import strip_rag_prefix, strip_sources_section, t
from app.services.line_messaging.flex.rag_answer_flex import (
    build_document_answer_flex,
    build_rag_answer_flex,
)
from app.services.line_messaging.token_manager import LineTokenManager
from resources.flex_messages.size_guard import fits
from resources.flex_messages.theme import resolve_theme

logger = logging.getLogger(__name__)

LOGGER_HEADER_TEXT = "[LineReplier]"
DEFAULT_AUDIO_DURATION_MS = 60_000


class LineReplier:
    """負責組織回覆內容並呼叫 LINE SDK 送出。"""

    def __init__(self, token_manager: LineTokenManager, tts_service=None) -> None:
        self._token_manager = token_manager
        self._tts_service = tts_service

    async def reply(
        self,
        reply_token: str,
        message_text: str,
        user_id: str,
        request_location: bool = False,
        voice_reply_enabled: bool = True,
        language: str | None = None,
        voice_rate: str = "normal",
        voice_gender: str = "female",
        answer_kind: str | None = None,
        user_question: str = "",
    ) -> bool:
        """發送 LINE 訊息（包含文字訊息、Flex Message 與選填的 TTS 語音訊息）"""
        try:
            if not reply_token or not reply_token.strip():
                raise ValueError("LINE 事件缺少 reply_token")
            if not user_id or not user_id.strip():
                raise ValueError("LINE 事件缺少 user_id")

            access_token = await self._token_manager.get_token_async()
            message_text = self._normalize_message_text(message_text)
            logger.info(
                f"{LOGGER_HEADER_TEXT} 準備回覆，user_id=%s, request_location=%s",
                user_id,
                request_location,
            )

            # 工具自產的 Flex（verify_claim、open_official_site）原樣送出。
            # 語音則看工具有沒有附朗讀稿：卡片 JSON 本身不能拿去合成，而只有
            # 工具知道哪一段是給耳朵的——判定卡有實質結論所以給，官網卡那種
            # 只有幾顆連結按鈕的就不給。沒附的維持原本的無語音行為。
            tool_flex, tool_speech_text = self._try_parse_flex_message(message_text)
            if tool_flex is not None:
                logger.info(
                    f"{LOGGER_HEADER_TEXT} 解析為工具 Flex Message，將以 Flex 形式回覆"
                )
                messages = [tool_flex]
                if tool_speech_text:
                    await self._append_tts_audio_message(
                        messages,
                        tool_speech_text,
                        voice_reply_enabled=voice_reply_enabled,
                        language=language,
                        voice_rate=voice_rate,
                        voice_gender=voice_gender,
                    )
            else:
                answer_card, card_text = self._build_answer_card(
                    message_text, answer_kind, user_question
                )
                if answer_card is not None:
                    logger.info(
                        f"{LOGGER_HEADER_TEXT} 已組成 %s 回答卡，將以 Flex 形式回覆",
                        answer_kind,
                    )
                    messages = [answer_card]
                    # 卡片路徑同樣附加語音：只有純文字分支有語音的話，開了語音
                    # 回覆的使用者會在 RAG 回覆上靜默失去這個功能。合成用的是
                    # 組卡前的純文字，不是卡片 JSON。
                    await self._append_tts_audio_message(
                        messages,
                        card_text,
                        voice_reply_enabled=voice_reply_enabled,
                        language=language,
                        voice_rate=voice_rate,
                        voice_gender=voice_gender,
                    )
                else:
                    logger.info(
                        f"{LOGGER_HEADER_TEXT} 未組成卡片，將以純文字回覆"
                    )
                    text_message = TextMessage(text=message_text)
                    messages = [text_message]
                    await self._append_tts_audio_message(
                        messages,
                        message_text,
                        voice_reply_enabled=voice_reply_enabled,
                        language=language,
                        voice_rate=voice_rate,
                        voice_gender=voice_gender,
                    )

            # quickReply 只會顯示在陣列最後一則訊息上，因此統一在此處掛到最後一則，
            # 避免 TTS 語音訊息排在文字訊息之後時，導致 Quick Reply 被 LINE 忽略。
            if request_location and messages:
                qr_label = t("location.share_qr_label", language=language)
                messages[-1].quick_reply = QuickReply(
                    items=[
                        QuickReplyItem(action=LocationAction(label=qr_label)),
                    ]
                )

            line_config = Configuration(access_token=access_token)
            with ApiClient(line_config) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        replyToken=reply_token,
                        messages=messages,
                    )
                )

            logger.debug("Message sent to LINE for user %s", user_id)
            return True

        except Exception:
            logger.exception("Failed to send LINE message")
            return False

    async def reply_flex(
        self, reply_token: str, flex_message: FlexMessage, user_id: str
    ) -> bool:
        """回覆 LINE Flex Message"""
        try:
            if not reply_token or not reply_token.strip():
                raise ValueError("LINE 事件缺少 reply_token")

            access_token = await self._token_manager.get_token_async()
            line_config = Configuration(access_token=access_token)
            with ApiClient(line_config) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.reply_message(
                    ReplyMessageRequest(
                        replyToken=reply_token,
                        messages=[flex_message],
                    )
                )
            logger.info("Flex Message replied to LINE user %s", user_id)
            return True
        except Exception:
            logger.exception("Failed to reply LINE Flex message")
            return False

    async def push_flex(self, user_id: str, flex_message: FlexMessage) -> bool:
        """主動推播 LINE Flex Message"""
        try:
            if not user_id or not user_id.strip():
                raise ValueError("LINE 推播缺少 user_id")

            access_token = await self._token_manager.get_token_async()
            line_config = Configuration(access_token=access_token)
            with ApiClient(line_config) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.push_message(
                    PushMessageRequest(
                        to=user_id,
                        messages=[flex_message],
                    )
                )
            logger.info("Flex Message pushed to LINE user %s", user_id)
            return True
        except Exception:
            logger.exception("Failed to push LINE Flex message")
            return False

    async def push_text(self, user_id: str, text: str) -> bool:
        """主動推播純文字訊息。

        背景通知（例如用藥風險提醒）沒有 reply token 可用，也不該佔用主回覆的
        reply token。純文字而非 Flex：這些訊息是說給當事人聽的一段話，不是卡片。
        """
        try:
            if not user_id or not user_id.strip():
                raise ValueError("LINE 推播缺少 user_id")

            access_token = self._token_manager.get_token()
            line_config = Configuration(access_token=access_token)
            with ApiClient(line_config) as api_client:
                line_bot_api = MessagingApi(api_client)
                line_bot_api.push_message(
                    PushMessageRequest(
                        to=user_id,
                        messages=[TextMessage(text=text)],
                    )
                )
            logger.info("Text message pushed to LINE user %s", user_id)
            return True
        except Exception:
            logger.exception("Failed to push LINE text message")
            return False


    def _build_answer_card(
        self, message_text: str, answer_kind: Optional[str], user_question: str
    ) -> tuple[Optional[FlexMessage], str]:
        """把 RAG 回覆組成卡片。

        回傳 `(卡片, 卡片內用的純文字)`；組不出來、太大或 answer_kind 為 None
        時卡片為 None。純文字一併回傳是給 TTS 用的——朗讀的內容應與卡片一致。

        任何失敗都退回純文字而非拋出：呈現層是最後一步，使用者寧可拿到樸素
        的文字，也不能拿到空白回覆。
        """
        if answer_kind not in ("rag", "document"):
            return None, message_text

        # 前綴由卡片 header 取代；來源清單移到按鈕，留在內文會重複一次。
        card_text = strip_sources_section(strip_rag_prefix(message_text)).strip()

        try:
            ft = resolve_theme()
            if answer_kind == "rag":
                card = build_rag_answer_flex(
                    user_question, card_text, get_request_rag_sources(), ft
                )
            else:
                card = build_document_answer_flex(user_question, card_text, ft)

            if not fits(card.to_dict()["contents"]):
                logger.warning(
                    f"{LOGGER_HEADER_TEXT} %s 回答卡超過大小上限，改以純文字回覆",
                    answer_kind,
                )
                return None, message_text

            return card, card_text
        except Exception:
            logger.warning(
                f"{LOGGER_HEADER_TEXT} %s 回答卡組裝失敗，改以純文字回覆",
                answer_kind,
                exc_info=True,
            )
            return None, message_text

    @staticmethod
    def _try_parse_flex_message(
        message_text: str,
    ) -> tuple[Optional[FlexMessage], str]:
        """解析工具自產的 Flex JSON，回傳 `(卡片, 朗讀稿)`。

        朗讀稿取自選填的頂層鍵 `speechText`，沒有就是空字串。它必須由工具提供
        而不能由這裡從卡片反解：這條路徑上 replier 拿到的只有 Flex JSON，組卡
        前的純文字沒有跨過 agent 邊界（對照 `_build_answer_card`，那條路是
        replier 自己組卡，所以純文字還在手上）。從已組好的卡片節點反解文字是
        另一個坑，理由同 `rag_sources.SourceRef` 的 docstring。

        `speechText` 只被讀走、不會進到送出的 FlexMessage——LINE 的 payload 裡
        沒有這個欄位。
        """
        if not message_text or not isinstance(message_text, str):
            return None, ""

        text_strip = message_text.strip()
        if not (text_strip.startswith("{") and text_strip.endswith("}")):
            return None, ""

        try:
            data = json.loads(text_strip)
        except json.JSONDecodeError:
            logger.debug(f"{LOGGER_HEADER_TEXT} 文字內容不是有效 JSON，略過 Flex 解析")
            return None, ""

        if not isinstance(data, dict):
            return None, ""

        if data.get("type") == "flex" and "contents" in data:
            alt_text = data.get("altText") or "醫療院所查詢結果"
            contents = FlexContainer.from_dict(data["contents"])
            speech_text = data.get("speechText")
            speech_text = speech_text.strip() if isinstance(speech_text, str) else ""
            logger.info(
                f"{LOGGER_HEADER_TEXT} Flex JSON 解析成功，altText=%s, has_speech=%s",
                alt_text,
                bool(speech_text),
            )
            return FlexMessage(altText=alt_text, contents=contents), speech_text

        return None, ""

    @staticmethod
    def _normalize_message_text(message_text: Any) -> str:
        if isinstance(message_text, str):
            return message_text
        if isinstance(message_text, list):
            return "".join(
                (
                    part
                    if isinstance(part, str)
                    else (part.get("text", "") if isinstance(part, dict) else str(part))
                )
                for part in message_text
            )
        if message_text is None:
            return ""
        return str(message_text)


    async def _append_tts_audio_message(
        self,
        messages: list,
        message_text: str,
        *,
        voice_reply_enabled: bool,
        language: str | None = None,
        voice_rate: str = "normal",
        voice_gender: str = "female",
    ) -> None:
        if not voice_reply_enabled or self._tts_service is None:
            return

        # 合成是在 reply_message 之前被 await 的，所以這段時間**直接加在**
        # agent＋RAG 之後。實測 4/5 使用者開了語音回覆，這是常態路徑而非
        # 邊緣，但整條路上只有這一段沒被量過。ok 欄位要分開記：失敗會轉
        # gTTS 備援或退回純文字，兩者的耗時意義不同。
        try:
            with stage_timer(
                logger, "reply_tts", chars=len(message_text or ""), ok="False"
            ) as t_tts:
                _audio_bytes, output, duration_ms = await self._tts_service.synthesize(
                    message_text,
                    language=language or "zh-TW",
                    voice_rate=voice_rate,
                    voice_gender=voice_gender,
                )
                t_tts["ok"] = "True"
            audio_url = self._resolve_audio_url(output)
        except Exception:
            logger.exception("TTS generation failed; falling back to text reply.")
            return

        if audio_url:
            messages.append(
                AudioMessage(
                    original_content_url=audio_url,
                    duration=int(duration_ms or DEFAULT_AUDIO_DURATION_MS),
                )
            )

    @staticmethod
    def _resolve_audio_url(output: str) -> Optional[str]:
        if output.startswith(("https://", "http" + "://")):
            return output

        audio_path = Path(output)
        if not settings.PUBLIC_BASE_URL.strip():
            logger.warning("PUBLIC_BASE_URL is not set; skipping LINE audio reply.")
            return None
        if not audio_path.exists():
            logger.warning("TTS output file not found: %s", audio_path)
            return None

        audio_url_path = settings.TTS_AUDIO_URL_PATH.strip("/") or "tts"
        return (
            f"{settings.PUBLIC_BASE_URL.rstrip('/')}/"
            f"{audio_url_path}/{quote(audio_path.name)}"
        )
