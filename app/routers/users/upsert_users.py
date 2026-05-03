from fastapi import APIRouter, Depends

from app.models.user import UserProfileData
from app.services.users.user_profile_service import UserProfileService
from app.dependencies import get_user_profile_service, get_current_user, CurrentUser

router = APIRouter(tags=["Profile"])


@router.put("/{user_id}")
async def upsert_user_profile(
    user_id: str,
    body: UserProfileData,
    current_user: CurrentUser = Depends(get_current_user),
    service: UserProfileService = Depends(get_user_profile_service),
):
    """
    更新使用者個人健康資料
    需要有效的 JWT 認證令牌
    """
    # 確保使用者只能修改自己的資料
    if current_user.line_user_id != user_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="無權修改其他使用者的資料")

    updated = await service.upsert_user_profile(user_id, body.model_dump())
    return {"user_id": user_id, "updated": updated}
