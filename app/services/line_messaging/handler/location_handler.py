import asyncio
import logging

from linebot.v3.webhooks import MessageEvent, LocationMessageContent
from app.services.line_messaging.handler.message_handler import BaseLineMessageHandler
from app.repositories.user_location_repository import UserLocationRepository
from app.core.request_logging import log_stage
from app.core.user_language import DEFAULT_USER_LANGUAGE, normalize_user_language
from app.i18n.messages import t

logger = logging.getLogger(__name__)
LOGGER_HEADER_TEXT = "[Handler:LocationHandler]"


class LineLocationHandler(BaseLineMessageHandler):
    """處理位置資訊訊息事件。"""

    async def handle(self, event: MessageEvent) -> None:
        message = event.message
        if not isinstance(message, LocationMessageContent):
            raise ValueError("Expected LocationMessageContent")
        #使用line內建方式取得user_id
        user_id = getattr(event.source, "user_id", None)
        if user_id:
            # 呼叫 save_location 暫存使用者位置資訊
            await UserLocationRepository.save_location(
                user_id=user_id,
                lat=message.latitude,
                lng=message.longitude,
            )
        # 走失求救進行中：這個位置是要給家人的，不是要找附近院所。長輩在定位頁
        # 打不開時會按聊天室的「傳送一次位置」（見 lost_location_flex_message）。
        if user_id and await self._record_lost_location(event, user_id, message):
            return

        logger.info(
            f"{LOGGER_HEADER_TEXT} 收到位置資訊，user_id=%s, lat=%s, lng=%s",
            user_id,
            message.latitude,
            message.longitude,
        )


        user_text = f"這是我的目前位置：lat={message.latitude}, lng={message.longitude}"
        logger.info(
            f"{LOGGER_HEADER_TEXT} 已轉換位置訊息為文字輸入，user_text=%s",
            user_text,
        )
        await self._process_and_reply(event, user_text, "location")

    async def _record_lost_location(
        self, event: MessageEvent, user_id: str, message: LocationMessageContent
    ) -> bool:
        """有進行中的求救時記下位置、回長輩一句話，回傳 True；否則回傳 False。"""
        service = self._lost_location_service
        if service is None:
            return False
        session = await service.record_location(
            user_id, lat=message.latitude, lng=message.longitude, source="line"
        )
        if session is None:
            return False

        language = DEFAULT_USER_LANGUAGE
        if self._user_profile_service:
            try:
                profile = await self._user_profile_service.get_user_profile(user_id)
                language = normalize_user_language(
                    self._language_choice_from_profile(profile)
                )
            except Exception:
                logger.warning("讀取使用者語言失敗，以預設語言回覆", exc_info=True)
        ok = await self._replier.reply(
            reply_token=getattr(event, "reply_token", ""),
            message_text=t("lost.elder.location_received", language),
            user_id=user_id,
            voice_reply_enabled=False,
            language=language,
        )
        log_stage(logger, "lost_location", source="line", ok=ok)

        if service.needs_location_started_notice(session):
            async def _notify() -> None:
                try:
                    await service.notify_location_started(session)
                except Exception:
                    logger.exception("走失位置通知任務失敗")

            task = asyncio.create_task(_notify())
            self._safety_alert_tasks.add(task)
            task.add_done_callback(self._safety_alert_tasks.discard)
        return True
