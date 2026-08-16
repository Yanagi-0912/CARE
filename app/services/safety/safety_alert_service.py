"""用藥風險偵測的協調者。

流程：前置篩選 → 字符集訊號 → 抽取 → 藥證庫比對 → 純函式判定 → 分級揭露。
本服務只負責把這些接起來，判定門檻一律留在 `risk_rules`。

對主流程 fail-open，對通報 fail-closed：任何一步失敗都記 log 後靜默結束，
不通報、不回覆、不影響主回覆。使用者並沒有在等這個結果，一則「風險偵測失敗」
只會造成困惑。

log 一律不帶輸入文字、藥名、姓名與機構——輸入可能是帶病情的提問，也可能是
同時含病患姓名與就診機構的藥袋 OCR 全文。
"""

import logging
from typing import Any

from app.core.user_font_size import DEFAULT_USER_FONT_SIZE, normalize_user_font_size
from app.core.user_language import DEFAULT_USER_LANGUAGE, normalize_user_language
from app.i18n import t
from app.models.safety import DrugMention
from app.services.line_messaging.flex.safety_flex import build_family_alert_flex
from app.services.safety.risk_rules import (
    assess,
    detect_foreign_scripts,
    looks_drug_related,
    normalize_drug_key,
)

logger = logging.getLogger(__name__)

# 泛稱：查不到姓名時仍要能發出通報，沿用 medication_scheduler 的同一個退路。
_FALLBACK_PATIENT_NAME = "成員"


class SafetyAlertService:
    def __init__(
        self,
        extractor: Any,
        catalog_service: Any,
        alert_repository: Any,
        family_tree_repository: Any,
        replier: Any,
        user_profile_service: Any = None,
        dedupe_hours: int = 24,
    ) -> None:
        self._extractor = extractor
        self._catalog_service = catalog_service
        self._alert_repository = alert_repository
        self._family_tree_repository = family_tree_repository
        self._replier = replier
        self._user_profile_service = user_profile_service
        self._dedupe_hours = dedupe_hours

    async def check(self, user_id: str, text: str) -> None:
        """對一段文字做一次風險評估。永遠不拋例外。"""
        try:
            await self._check(user_id, text)
        except Exception as exc:  # noqa: BLE001 - 背景旁路，例外不得逸散
            logger.warning("用藥風險評估失敗，本次結束：%s", type(exc).__name__)

    async def _check(self, user_id: str, text: str) -> None:
        if not user_id or not text:
            return

        # 藥證庫缺席時判定只剩一半的資料，寧可不判定也不要在缺資料的情況下
        # 通報（未命中會全部落到 low，等於對每一個藥名都打擾當事人一次）。
        if self._catalog_service is None or self._catalog_service.is_empty:
            logger.warning("藥證庫缺席，本次不進行用藥風險判定")
            return

        if not looks_drug_related(text, self._catalog_service):
            return

        foreign_scripts = detect_foreign_scripts(text)
        mentions = await self._extractor.extract(text)

        for mention in mentions:
            resolved = self._with_catalog_result(mention)
            risk_level = assess(resolved, foreign_scripts)
            if risk_level == "none":
                continue
            if risk_level == "low":
                await self._notify_patient_low(user_id, resolved)
            else:
                await self._notify_high(user_id, resolved, foreign_scripts)

    def _with_catalog_result(self, mention: DrugMention) -> DrugMention:
        """把藥證庫比對結果補回抽取結果。

        抽取器不做這件事：`catalog_hit` 是外部字典的事實，不是模型的判斷。
        命中與否要看 `match()` 是不是 None，而不是看 license_number
        （含容命中多張藥證時證號為 None，但藥名確實已被驗證存在）。
        """
        match = self._catalog_service.match(mention.raw_name)
        return mention.model_copy(
            update={
                "catalog_hit": match is not None,
                "license_number": match.license_number if match else None,
            }
        )

    async def _notify_patient_low(self, user_id: str, mention: DrugMention) -> None:
        """`low` 只回當事人，SHALL NOT 通報任何其他人。

        透明不等於動不動就叫人：證據只有「藥證庫查無」時，最可能的原因是俗稱、
        簡稱或錯字。
        """
        language, _ = await self._resolve_display_prefs(user_id)
        await self._replier.push_text(
            user_id,
            t("safety.patient.low", language).format(drug=mention.raw_name),
        )

    async def _notify_high(
        self, user_id: str, mention: DrugMention, foreign_scripts: list[str]
    ) -> None:
        """`high` 通報族譜全員，並於同一次流程告知當事人。

        先取得通報權再送任何訊息：沒取得代表節流視窗內已經通報過，這時連當事人
        都不再打擾（同一件事講兩次沒有新資訊）。
        """
        drug_key = normalize_drug_key(mention.raw_name)
        granted = await self._alert_repository.try_claim(
            user_id=user_id,
            drug_key=drug_key,
            risk_level="high",
            ttl_hours=self._dedupe_hours,
        )
        if not granted:
            return

        reason_key = (
            "safety.reason.foreign_version"
            if foreign_scripts or mention.channel == "overseas_personal"
            else "safety.reason.unverified_channel"
        )

        await self._alert_family(user_id, mention, reason_key)

        language, _ = await self._resolve_display_prefs(user_id)
        await self._replier.push_text(
            user_id,
            t("safety.patient.high", language).format(
                drug=mention.raw_name, reason=t(reason_key, language)
            ),
        )

    async def _alert_family(
        self, user_id: str, mention: DrugMention, reason_key: str
    ) -> None:
        """通報族譜全員。

        沒有族譜、成員為空或查詢失敗都不是錯誤路徑的終點：當事人那則仍然要送，
        因此這裡把例外吃掉而不是往外拋。
        """
        member_ids = await self._resolve_family_member_ids(user_id)
        if not member_ids:
            return

        patient_name = await self._resolve_patient_name(user_id)
        for member_id in member_ids:
            # 語言與字級是收件家人本人的設定，不是當事人的。
            language, font_size = await self._resolve_display_prefs(member_id)
            flex = build_family_alert_flex(
                patient_name=patient_name,
                drug_name=mention.raw_name,
                risk_reason=t(reason_key, language),
                language=language,
                font_size=font_size,
            )
            await self._replier.push_flex(member_id, flex)

    async def _resolve_family_member_ids(self, user_id: str) -> list[str]:
        try:
            tree = await self._family_tree_repository.get_by_user_id(user_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("族譜查詢失敗，本次不通報家人：%s", type(exc).__name__)
            return []

        members = getattr(tree, "family_members", None) or []
        return [member.user_id for member in members if member.user_id]

    async def _resolve_patient_name(self, user_id: str) -> str:
        if not self._user_profile_service:
            return _FALLBACK_PATIENT_NAME
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:  # noqa: BLE001
            return _FALLBACK_PATIENT_NAME
        if isinstance(profile, dict) and profile.get("name"):
            return profile["name"]
        return _FALLBACK_PATIENT_NAME

    async def _resolve_display_prefs(self, user_id: str) -> tuple[str, str]:
        """逐一取收件人自己的語言與字級。背景推播沒有 request context 可用。"""
        if not self._user_profile_service or not user_id:
            return DEFAULT_USER_LANGUAGE, DEFAULT_USER_FONT_SIZE
        try:
            profile = await self._user_profile_service.get_user_profile(user_id)
        except Exception:  # noqa: BLE001
            return DEFAULT_USER_LANGUAGE, DEFAULT_USER_FONT_SIZE

        settings: dict = (profile or {}).get("settings") or {}
        return (
            normalize_user_language(settings.get("language")),
            normalize_user_font_size(settings.get("font_size")),
        )
