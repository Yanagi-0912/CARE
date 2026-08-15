import logging
from typing import TYPE_CHECKING

from app.models.user import UserProfile, UserSettings, UserSettingsUpdate
from app.repositories.user_profile_repository import UserProfileRepository

if TYPE_CHECKING:
    from app.services.line_messaging.rich_menu_service import RichMenuService

logger = logging.getLogger(__name__)


class UserProfileService:
    """使用者健康資料服務層。"""

    def __init__(
        self,
        repo: UserProfileRepository,
        rich_menu_service: "RichMenuService | None" = None,
    ) -> None:
        self._repo = repo
        self._rich_menu_service = rich_menu_service

    async def upsert_user_profile(self, line_id: str, payload: dict) -> bool:
        profile = UserProfile.from_upsert(line_id=line_id, payload=payload)
        normalized_payload = profile.to_payload()
        return await self._repo.upsert_user_profile(line_id, normalized_payload)

    async def get_user_profile(self, line_id: str):
        return await self._repo.get_user_profile(line_id)

    async def update_voice_reply_enabled(self, line_id: str, enabled: bool) -> bool:
        return await self._repo.update_voice_reply_enabled(line_id, enabled)

    async def sync_line_profile(
        self,
        line_id: str,
        *,
        picture_url: str | None = None,
    ) -> bool:
        return await self._repo.sync_line_profile(
            line_id,
            picture_url=picture_url,
        )

    async def get_user_settings(self, line_id: str) -> dict:
        profile = await self._repo.get_user_profile(line_id)
        raw_settings = (profile or {}).get("settings") or {}
        return UserSettings(**raw_settings).model_dump()

    async def update_user_settings(
        self, line_id: str, update: UserSettingsUpdate
    ) -> dict:
        changed_fields = update.model_dump(exclude_unset=True, exclude_none=True)
        if changed_fields:
            updated = await self._repo.update_user_settings(line_id, changed_fields)
            if (
                updated
                and "language" in changed_fields
                and self._rich_menu_service is not None
            ):
                try:
                    self._rich_menu_service.link_user_menu(
                        line_id, changed_fields["language"]
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to link rich menu after language change: %s", exc
                    )
        return await self.get_user_settings(line_id)

    async def create_default_user_profile(
        self,
        line_id: str,
        display_name: str | None = None,
        picture_url: str | None = None,
        language: str | None = None,
    ) -> bool:
        default_payload = {
            "name": (display_name or "LINE User").strip() or "LINE User",
            "gender": "unknown",
            "height": 1.0,
            "weight": 1.0,
            "age": 0,
            "chronic_diseases": [],
            "chronic_custom": [],
            "major_illness_history": "",
            "surgery_history": "",
            "health_consultations": {},
            "picture_url": picture_url,
            "settings": UserSettings(language=language).model_dump(),
        }
        return await self.upsert_user_profile(line_id=line_id, payload=default_payload)
