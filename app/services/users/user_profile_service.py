from app.models.user import UserProfile, UserSettings, UserSettingsUpdate
from app.repositories.user_profile_repository import UserProfileRepository


class UserProfileService:
    """使用者健康資料服務層。"""

    def __init__(self, repo: UserProfileRepository) -> None:
        self._repo = repo

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
            await self._repo.update_user_settings(line_id, changed_fields)
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
            "chronic_history": "",
            "major_illness_history": "",
            "surgery_history": "",
            "health_consultations": {},
            "picture_url": picture_url,
            "settings": UserSettings(language=language).model_dump(),
        }
        return await self.upsert_user_profile(line_id=line_id, payload=default_payload)
