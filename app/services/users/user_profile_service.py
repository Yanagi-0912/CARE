from app.models.user import UserProfile, UserSettings, UserSettingsUpdate
from app.repositories.user_profile_repository import UserProfileRepository


class UserProfileService:
    """使用者健康資料服務層。"""

    def __init__(self, repo: UserProfileRepository) -> None:
        self._repo = repo

    async def upsert_user_profile(self, line_id: str, payload: dict) -> bool:
        """
        驗證 payload 後寫入資料庫。

        chronic_history 目前固定使用字串格式。
        """

        profile = UserProfile.from_upsert(line_id=line_id, payload=payload)
        normalized_payload = profile.to_payload()
        return await self._repo.upsert_user_profile(line_id, normalized_payload)

    async def get_user_profile(self, line_id: str):
        """
        從資料庫取得使用者個人健康資料。
        """
        return await self._repo.get_user_profile(line_id)

    async def sync_line_profile(
        self,
        line_id: str,
        *,
        picture_url: str | None = None,
    ) -> bool:
        """同步 LINE profile 欄位至 MongoDB，不觸發健康資料驗證。"""
        return await self._repo.sync_line_profile(
            line_id,
            picture_url=picture_url,
        )

    async def get_user_settings(self, line_id: str) -> dict:
        """
        取得使用者介面偏好設定。

        若資料庫尚未寫入 settings（例如舊資料、尚未登入過新版），
        則回傳預設值，不會噴錯。
        """
        profile = await self._repo.get_user_profile(line_id)
        raw_settings = (profile or {}).get("settings") or {}
        return UserSettings(**raw_settings).model_dump()

    async def update_user_settings(
        self, line_id: str, update: UserSettingsUpdate
    ) -> dict:
        """
        只更新使用者實際帶入的設定欄位，其餘欄位維持不變。

        回傳更新後的完整設定（合併資料庫原值 + 這次變更）。
        """
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
        """
        建立初始使用者資料，供首次登入且尚未填寫健康資料者使用。

        language 只在「首次建立」時寫入一次（作為預設值）；
        之後使用者若在前端手動變更語言，一律以資料庫的值為準。
        """
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
