from fastapi import APIRouter, Depends
from app.schemas import ProfileUpsertRequest, ProfileUpsertResponse
from app.application.users.profile_service import ProfileService
from app.dependencies import get_user_profile_service

router = APIRouter(tags=["Profile"])  #


@router.put("/{user_id}", response_model=ProfileUpsertResponse)
async def upsert_user_profile(
    user_id: str,
    body: ProfileUpsertRequest,
    service: ProfileService = Depends(get_user_profile_service),
):
    updated = await service.upsert_user_profile(user_id, body.model_dump())
    return ProfileUpsertResponse(user_id=user_id, updated=updated)
