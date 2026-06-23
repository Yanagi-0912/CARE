from app.models.user import UserProfile
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

    async def update_voice_reply_enabled(self, line_id: str, enabled: bool) -> bool:
        return await self._repo.update_voice_reply_enabled(line_id, enabled)

    async def create_default_user_profile(
        self,
        line_id: str,
        display_name: str | None = None,
        picture_url: str | None = None,
    ) -> bool:
        """
        建立初始使用者資料，供首次登入且尚未填寫健康資料者使用。
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
            "voice_reply_enabled": True,
        }
        return await self.upsert_user_profile(line_id=line_id, payload=default_payload)
