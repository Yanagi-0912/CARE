from fastapi import APIRouter, Depends, HTTPException

from app.models.user import UserProfileData
from app.services.users.user_profile_service import UserProfileService
from app.services.family.family_tree_service import FamilyTreeService
from app.dependencies import (
    get_user_profile_service,
    get_family_tree_service,
    get_current_user,
    CurrentUser,
)
from fastapi import HTTPException

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


@router.get(
    "/{userId}",
    summary="取得指定使用者的健康資料",
    description="取得指定使用者的健康資料。出於安全考量，請求者與目標使用者必須在同一個家庭族譜內。",
)
async def get_member_profile(
    userId: str,
    current_user: CurrentUser = Depends(get_current_user),
    profile_service: UserProfileService = Depends(get_user_profile_service),
    family_service: FamilyTreeService = Depends(get_family_tree_service),
):
    """
    取得指定使用者的健康資料（限同家庭成員）。
    """
    requester_id = current_user.line_user_id

    # 1. 如果是查詢自己，直接允許
    if requester_id == userId:
        profile = await profile_service.get_user_profile(userId)
        if not profile:
            raise HTTPException(status_code=404, detail="找不到使用者資料")
        return profile

    # 2. 檢查目標使用者是否在請求者的家庭內
    family_tree = await family_service.get_family_tree(requester_id)
    is_member = any(m.user_id == userId for m in family_tree.family_members)

    if not is_member:
        raise HTTPException(
            status_code=403,
            detail="您無權查看此使用者的健康資料（非家庭成員）",
        )

    # 3. 獲取健康資料
    profile = await profile_service.get_user_profile(userId)
    if not profile:
        raise HTTPException(status_code=404, detail="找不到該成員的健康資料")
    return profile
