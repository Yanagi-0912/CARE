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
