from fastapi import APIRouter, Depends

from app.models.user import UserProfileData
from app.services.users.user_profile_service import UserProfileService
from app.dependencies import get_user_profile_service, get_current_user, CurrentUser

router = APIRouter(tags=["Profile"])


@router.get(
    "/me",
    summary="取得目前登入使用者個人健康資料",
    description="回傳目前登入使用者的健康資料，需要有效的 JWT 認證令牌。",
)
async def get_user_profile(
    current_user: CurrentUser = Depends(get_current_user),
    service: UserProfileService = Depends(get_user_profile_service),
):
    """
    取得目前登入使用者個人健康資料。
    """
    user_id = current_user.line_user_id
    profile = await service.get_user_profile(user_id)
    if not profile:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="找不到使用者資料")
    return profile


@router.put(
    "/me/update",
    summary="更新目前登入使用者個人健康資料",
    description="更新或建立目前登入使用者的健康資料，需要帶上有效的 JWT 認證令牌。",
)
async def upsert_user_profile(
    body: UserProfileData,
    current_user: CurrentUser = Depends(get_current_user),
    service: UserProfileService = Depends(get_user_profile_service),
):
    """
    更新目前登入使用者個人健康資料。
    """
    user_id = current_user.line_user_id
    updated = await service.upsert_user_profile(user_id, body.model_dump())
    return {"user_id": user_id, "updated": updated}
