from fastapi import APIRouter, Depends
from app.models.user import UserProfileData
from app.services.users.user_profile_service import UserProfileService
from app.dependencies import get_user_profile_service

router = APIRouter(tags=["Profile"])  #


@router.put("/{user_id}")
async def upsert_user_profile(
    user_id: str,
    body: UserProfileData,
    service: UserProfileService = Depends(get_user_profile_service),
):
    updated = await service.upsert_user_profile(user_id, body.model_dump())
    return {"user_id": user_id, "updated": updated}
