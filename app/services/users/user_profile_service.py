import logging
from typing import TYPE_CHECKING

from app.models.family_authorization import PROXY_WRITE_FORBIDDEN_FIELDS
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

    async def update_health_fields(self, line_id: str, payload: dict) -> bool:
        """部分更新健康欄位：**只寫入 payload 實際帶到的鍵**。

        代理寫入（GUARDIAN 代被照顧者填健康資料）不能共用 `upsert_user_profile`，
        有兩個各自獨立的理由：

        1. 那支會用 `UserProfile` 重建**完整**模型，而 `name` 是必填。代理寫入
           刻意不帶 `name`（顯示名稱不歸這條路徑管），於是直接 ValidationError，
           對外表現成 500。
        2. 更嚴重的是，重建出來的模型會把 `picture_url` 補成 `None`、`settings`
           補成一整組預設值，再一起 `$set` 回資料庫——那會清掉被照顧者的頭像與
           介面偏好。代理寫入 SHALL NOT 觸碰這些欄位。

        值的驗證在 router 就完成了（body 宣告為 `UserProfileData`，FastAPI 會在
        進入處理函式之前驗完），因此這裡不再重建模型；重建正是問題的來源。

        底層 repository 的 `$set` 本來就只寫 payload 裡有的鍵，少帶的欄位會維持
        資料庫既有的值，這正是部分更新要的語意。
        """
        forbidden = sorted(set(payload) & PROXY_WRITE_FORBIDDEN_FIELDS)
        if forbidden:
            # 呼叫端的程式錯誤，不是使用者的輸入問題——這條路徑永遠不該寫到
            # 身分識別或系統欄位。repository 的 $set 只 pop 掉 role，其餘照寫，
            # 所以這道守門必須在這裡。
            raise ValueError(
                f"代理寫入不得修改這些欄位：{forbidden}。"
                "顯示名稱與頭像需經由獨立的 profile-management 授權。"
            )
        return await self._repo.upsert_user_profile(line_id, payload)

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
