from fastapi import APIRouter, Depends, HTTPException

from app.models.family_tree import (
    FamilyTree,
    GetFamilyTreeResponse,
    CreateInviteResponse,
    VerifyInviteResponse,
    AcceptInviteRequest,
    AcceptInviteResponse,
    SetRelationshipRequest,
)
from app.services.family.family_tree_service import FamilyTreeService
from app.dependencies import get_family_tree_service, get_current_user, CurrentUser

router = APIRouter()


@router.get(
    "/me",
    response_model=GetFamilyTreeResponse,
    summary="取得個人家庭",
    description="取得目前登入使用者的家庭成員資料。",
)
async def get_my_tree(
    current_user: CurrentUser = Depends(get_current_user),
    service: FamilyTreeService = Depends(get_family_tree_service),
):
    tree = await service.get_family_tree(current_user.line_user_id)
    return GetFamilyTreeResponse(family_tree=tree)


@router.post(
    "/relationship",
    response_model=FamilyTree,
    summary="設定成員關係",
    description="設定目前使用者與其族譜內特定成員之間的關係。",
)
async def set_relationship(
    req: SetRelationshipRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: FamilyTreeService = Depends(get_family_tree_service),
):
    return await service.set_relationship(
        user_id=current_user.line_user_id,
        member_id=req.member_id,
        relationship_type=req.relationship_type,
    )


@router.post(
    "/invites",
    response_model=CreateInviteResponse,
    summary="產生邀請碼",
    description="根據目前登入的使用者產生一個隨機邀請碼。",
)
async def create_invite(
    current_user: CurrentUser = Depends(get_current_user),
    service: FamilyTreeService = Depends(get_family_tree_service),
):
    return await service.create_invitation(current_user.line_user_id)


@router.get(
    "/invites/verify/{code}",
    response_model=VerifyInviteResponse,
    summary="驗證邀請碼",
    description="驗證邀請碼是否有效。此為公開 API，不需要認證。",
)
async def verify_invite(
    code: str, service: FamilyTreeService = Depends(get_family_tree_service)
):
    """驗證邀請碼效期與資訊。"""
    return await service.verify_invitation(code)


@router.post(
    "/invites/accept",
    response_model=AcceptInviteResponse,
    summary="接受邀請",
    description="受邀者登入後，正式加入家族。",
)
async def accept_invite(
    req: AcceptInviteRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: FamilyTreeService = Depends(get_family_tree_service),
):
    """正式接受邀請並加入家族。"""
    return await service.accept_invitation(
        invitee_id=current_user.line_user_id, code=req.code
    )
