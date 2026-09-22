"""看診錄音轉成文字之後，用 LINE 告訴該知道的人。

錄音頁送出後只會說「好了會通知你」，所以這一則推播是那句話的兌現：沒有它，使用者
只能自己一直回來看，而家人根本不知道有這份紀錄。

### 誰收得到

- **整理好了**：就診者本人、按下錄音的人（家人陪診時是他），以及
  `notification_recipients(就診者, "clinic_visit_ready")`——也就是看得到這份紀錄的
  GUARDIAN 與 CAREGIVER（政策與理由見 `app/models/family_authorization.py`）。
- **沒有成功**：只送按下錄音的人。他是唯一在等結果的人；其他家人收到一則「沒了」
  不能做任何事，只會多一則訊息。

本人與錄音的人一定送：這則推播是他自己按送出時被承諾的。其餘家人尊重他在設定裡
關掉的「家人通知」（`settings.notify_family`），理由同走失與緊急通報。

推播失敗一律吞掉。紀錄已經寫進資料庫，推不出去只是少一則提醒，不能讓它把背景工作
連同 log 一起炸掉。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import quote

from app.core.user_font_size import DEFAULT_USER_FONT_SIZE, normalize_user_font_size
from app.core.user_language import DEFAULT_USER_LANGUAGE, normalize_user_language
from app.i18n import t
from app.models.clinic_transcript import ClinicVisitRecord
from app.services.line_messaging.flex.clinic_visit_flex import (
    build_clinic_visit_flex,
    summary_sections,
)
from app.services.line_messaging.rich_menu_layout import liff_uri
from resources.flex_messages.size_guard import fits

logger = logging.getLogger(__name__)

NOTIFICATION_KIND = "clinic_visit_ready"
# LINE 文字訊息上限 5,000 字。
_TEXT_MAX = 5000


@dataclass(frozen=True)
class _Prefs:
    language: str
    font_size: str
    notify_family: bool


class ClinicVisitNotifier:
    def __init__(
        self,
        *,
        replier: Any,
        authorization_service: Any,
        user_profile_service: Any = None,
        liff_url: str = "",
    ) -> None:
        self._replier = replier
        self._authorization_service = authorization_service
        self._user_profile_service = user_profile_service
        self._liff_url = (liff_url or "").strip()

    def record_url(self, record: ClinicVisitRecord, viewer_id: str) -> Optional[str]:
        """就診者本人不帶 target_user_id；家人要帶，API 才知道是在看誰的紀錄。"""
        if not self._liff_url or not record.id:
            return None
        path = f"/clinic-visits/{quote(record.id, safe='')}"
        if viewer_id != record.user_id:
            path += f"?target_user_id={quote(record.user_id, safe='')}"
        return liff_uri(self._liff_url, path)

    async def notify_ready(self, record: ClinicVisitRecord) -> None:
        try:
            owner_id = record.user_id
            always = [owner_id]
            if record.created_by_user_id and record.created_by_user_id != owner_id:
                always.append(record.created_by_user_id)
            family = [
                uid for uid in await self._family_recipients(owner_id) if uid not in always
            ]
            owner_name = await self._name(owner_id)

            for viewer_id in always + family:
                prefs = await self._prefs(viewer_id)
                if viewer_id in family and not prefs.notify_family:
                    logger.info("stage=clinic_notify 收件人關閉了家人通知，略過")
                    continue
                if viewer_id == owner_id:
                    body = t("flex.clinic.ready.body.self", prefs.language)
                else:
                    name = owner_name or t("emergency_family.fallback_name", prefs.language)
                    body = t("flex.clinic.ready.body.family", prefs.language).format(name=name)
                await self._push_card(
                    viewer_id,
                    record,
                    prefs,
                    header=t("flex.clinic.ready.header", prefs.language),
                    body=body,
                    ready=True,
                )
            logger.info(
                "stage=clinic_notify ready record=%s recipients=%d",
                record.id,
                len(always) + len(family),
            )
        except Exception:  # noqa: BLE001 - 推播是加值，失敗不得影響紀錄
            logger.warning("stage=clinic_notify ready 失敗 record=%s", record.id, exc_info=True)

    async def notify_failed(self, record: ClinicVisitRecord) -> None:
        try:
            viewer_id = record.created_by_user_id or record.user_id
            prefs = await self._prefs(viewer_id)
            await self._push_card(
                viewer_id,
                record,
                prefs,
                header=t("flex.clinic.failed.header", prefs.language),
                body=t("flex.clinic.failed.body", prefs.language),
            )
            logger.info("stage=clinic_notify failed record=%s", record.id)
        except Exception:  # noqa: BLE001 - 同 notify_ready
            logger.warning("stage=clinic_notify failed 失敗 record=%s", record.id, exc_info=True)

    async def _push_card(
        self,
        viewer_id: str,
        record: ClinicVisitRecord,
        prefs: _Prefs,
        *,
        header: str,
        body: str,
        ready: bool = False,
    ) -> None:
        # 整理好的卡片直接放摘要（2026-09-22 起，見 clinic_visit_flex 的說明）。
        summary = record.summary if ready else None
        flex = build_clinic_visit_flex(
            header=header,
            body_text=body,
            hospital_name=record.hospital_name,
            open_url=self.record_url(record, viewer_id),
            language=prefs.language,
            font_size=prefs.font_size,
            summary=summary,
            self_recap=ready and record.consent == "self_recap",
        )
        try:
            if fits(flex.contents.to_dict()) and await self._replier.push_flex(viewer_id, flex):
                return
        except Exception:  # noqa: BLE001
            logger.warning("stage=clinic_notify Flex 推播失敗，改送純文字", exc_info=True)
        lines = [header, body]
        if summary is not None:
            # Flex 太大送不出去時，摘要照樣要到：純文字版同一份內容。
            for title, items in summary_sections(summary, prefs.language):
                lines.append(f"\n【{title}】")
                lines.extend(f"・{item}" for item in items)
        try:
            await self._replier.push_text(viewer_id, "\n".join(lines)[:_TEXT_MAX])
        except Exception:  # noqa: BLE001
            logger.warning("stage=clinic_notify 純文字推播也失敗", exc_info=True)

    async def _family_recipients(self, owner_id: str) -> list[str]:
        """判定失敗視為沒有家人收件人（不猜），本人與錄音者照送。"""
        if self._authorization_service is None:
            return []
        try:
            recipients = await self._authorization_service.notification_recipients(
                owner_id, NOTIFICATION_KIND
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("stage=clinic_notify 收件人判定失敗：%s", type(exc).__name__)
            return []
        unique: list[str] = []
        for uid in recipients or []:
            if uid and uid != owner_id and uid not in unique:
                unique.append(uid)
        return unique

    async def _profile(self, user_id: str) -> dict[str, Any]:
        if not self._user_profile_service or not user_id:
            return {}
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:  # noqa: BLE001
            return {}
        return profile if isinstance(profile, dict) else {}

    async def _name(self, user_id: str) -> str:
        return (await self._profile(user_id)).get("name") or ""

    async def _prefs(self, user_id: str) -> _Prefs:
        settings = (await self._profile(user_id)).get("settings") or {}
        return _Prefs(
            normalize_user_language(settings.get("language") or DEFAULT_USER_LANGUAGE),
            normalize_user_font_size(settings.get("font_size") or DEFAULT_USER_FONT_SIZE),
            # 欄位缺席時視為開啟，同緊急通報。
            bool(settings.get("notify_family", True)),
        )
