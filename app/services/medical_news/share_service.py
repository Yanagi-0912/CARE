"""「認同，分享給家人」的處理。

**這條路徑刻意不走 `FamilyAuthorizationService.notification_recipients()`。**
那張表（`NOTIFICATION_POLICY`）回答的是「這位當事人出事時該通知誰」，其
docstring 已載明它與 `PERMISSIONS` 刻意分離，因為「他可以隨時看我的健康資料」
與「我出事時要通知他」是兩種不同的信任。

「我主動分享一則公開消息給他」是**第三種**信任，而且門檻最低——分享卡零洩漏，
內容是任何人都能在官網上看到的公開資訊。把它塞進 `NOTIFICATION_POLICY` 會讓
兩種語意互相污染：日後有人調整通報政策，會在毫無關聯的地方改變分享行為
（design.md 決策 7）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from app.i18n import t
from app.models.medication import TAIPEI_TZ
from app.repositories.medical_news_repository import (
    MedicalNewsDeliveryRepository,
    MedicalNewsShareRepository,
)
from app.services.line_messaging.flex.medical_news_flex import build_shared_news_flex
from app.services.line_messaging.reply.reply import LineReplier

logger = logging.getLogger(__name__)

_FALLBACK_SHARER_NAME = "家人"


class MedicalNewsShareService:
    def __init__(
        self,
        *,
        replier: LineReplier,
        family_tree_service: Any,
        user_profile_service: Any,
        daily_share_limit: int,
        delivery_repository: Any = MedicalNewsDeliveryRepository,
        share_repository: Any = MedicalNewsShareRepository,
        family_authorization_service: Any = None,
    ) -> None:
        self._replier = replier
        self._family_tree_service = family_tree_service
        self._user_profile_service = user_profile_service
        self._daily_share_limit = daily_share_limit
        self._delivery_repository = delivery_repository
        self._share_repository = share_repository
        # 只為了讓測試能斷言「它沒有被呼叫」而收下這個相依。正式路徑不使用它，
        # 理由見模組 docstring。
        self._family_authorization_service = family_authorization_service

    async def share(
        self,
        *,
        sharer_id: str,
        news_ref: str,
        reply_token: str,
        language: str | None = None,
        font_size: str | None = None,
    ) -> None:
        delivery = await self._delivery_repository.find(sharer_id, news_ref)
        if delivery is None:
            # 使用者可能點到很久以前的卡片，而該筆紀錄已被清掉。回一句話比
            # 沒有反應好——按了按鈕卻什麼都沒發生，使用者會一直重按。
            await self._reply(reply_token, sharer_id, t("news.share_expired", language), language)
            return

        used = await self._delivery_repository.count_shares_today(
            sharer_id, _today_start()
        )
        if used >= self._daily_share_limit:
            await self._reply(
                reply_token, sharer_id, t("news.share_limit_reached", language), language
            )
            return

        recipients = await self._resolve_recipients(sharer_id)
        if not recipients:
            await self._reply(
                reply_token, sharer_id, t("news.no_family", language), language
            )
            return

        sharer_name = await self._resolve_display_name(sharer_id)
        sent = 0
        for recipient_id in recipients:
            # 先搶後送：同一則對同一位收件人只送一次，不論幾位家人都按了認同。
            if not await self._share_repository.claim(
                recipient_id, news_ref, sharer_id
            ):
                continue
            if await self._send_to(recipient_id, delivery, sharer_name):
                sent += 1

        await self._delivery_repository.mark_shared(sharer_id, news_ref, sent)
        message = (
            t("news.shared_ok", language).format(count=sent)
            if sent
            else t("news.shared_none", language)
        )
        await self._reply(reply_token, sharer_id, message, language)

    async def _resolve_recipients(self, sharer_id: str) -> list[str]:
        """族譜成員，扣掉分享者本人。

        SHALL NOT 呼叫 `notification_recipients()`——見模組 docstring。
        """
        tree = await self._family_tree_service.get_family_tree(sharer_id)
        return [
            member.user_id
            for member in tree.family_members
            if member.user_id and member.user_id != sharer_id
        ]

    async def _send_to(self, recipient_id: str, delivery: Any, sharer_name: str) -> bool:
        """組出去個人化的卡片並送給一位收件人。

        傳給 builder 的只有 `delivery` 上的四個欄位——標題、摘要、來源名、網址。
        摘要在索引階段就已被要求寫成中性第三人稱，因此這裡不需要（也不做）任何
        文字改寫；藥名與 tier 標示根本沒有進入 builder 的介面。
        """
        language, font_size = await self._resolve_display_prefs(recipient_id)
        try:
            flex = build_shared_news_flex(
                sharer_name=sharer_name,
                title=delivery.title,
                summary=delivery.summary,
                source_name=delivery.source_name,
                url=delivery.url,
                language=language,
                font_size=font_size,
            )
        except ValueError:
            logger.warning(
                "[MedicalNewsShare] 卡片超過大小上限，略過收件人 %s", recipient_id
            )
            return False

        # 單一收件人推播失敗只記 log：其餘收件人仍應收到。
        return bool(await self._replier.push_flex(recipient_id, flex))

    async def _resolve_display_name(self, user_id: str) -> str:
        if not self._user_profile_service:
            return _FALLBACK_SHARER_NAME
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:
            logger.exception("[MedicalNewsShare] 無法載入分享者名稱：%s", user_id)
            return _FALLBACK_SHARER_NAME
        if isinstance(profile, dict) and profile.get("name"):
            return str(profile["name"])
        return _FALLBACK_SHARER_NAME

    async def _resolve_display_prefs(self, user_id: str) -> tuple[Optional[str], Optional[str]]:
        """收件人的語言與字級。卡片是送給**收件人**的，設定要取他本人的。"""
        if not self._user_profile_service:
            return None, None
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:
            logger.exception("[MedicalNewsShare] 無法載入收件人設定：%s", user_id)
            return None, None
        settings = (profile or {}).get("settings") or {}
        return settings.get("language"), settings.get("font_size")

    async def _reply(
        self, reply_token: str, user_id: str, message: str, language: str | None
    ) -> None:
        await self._replier.reply(
            reply_token=reply_token,
            message_text=message,
            user_id=user_id,
            voice_reply_enabled=False,
            language=language,
        )


def _today_start() -> datetime:
    """台北時間的今日零時。分享次數上限以使用者感知的「今天」計算。"""
    now = datetime.now(TAIPEI_TZ)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
