from app.repositories.user_profile_repository import UserProfileRepository

class ProfileService:
    def __init__(self, repo: UserProfileRepository) -> None:
        self._repo = repo

    async def upsert_profile(self, line_id: str, payload: dict) -> bool:
        return await self._repo.upsert_profile(line_id, payload)