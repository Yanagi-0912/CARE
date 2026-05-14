from fastapi import APIRouter, Depends, HTTPException
from typing import List

from app.models.family_tree import (
    FamilyTree,
    SendInvitationRequest,
    SendInvitationResponse,
    AddToFamilyRequest,
    AddToFamilyResponse,
    SetRelationshipRequest,
    GetFamilyTreeResponse,
)
from app.services.family.family_tree_service import FamilyTreeService
from app.dependencies import get_family_tree_service

router = APIRouter()

# ----------------------------------------------------------------------
#  取得個人族譜 (Get Family Tree)
# ----------------------------------------------------------------------
@router.get(
    "/me",
    response_model=GetFamilyTreeResponse,
    summary="取得個人族譜",
    description="取得指定使用者的家族樹。如果尚未建立，則初始化並回傳空族譜。",
)
async def get_my_tree(
    user_id: str,
    service: FamilyTreeService = Depends(get_family_tree_service)
):
    """
    取得指定使用者的家族樹。如果尚未建立，則初始化並回傳空族譜。
    """
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user_id")
    
    tree = await service.get_family_tree(user_id)
    return GetFamilyTreeResponse(family_tree=tree)

# ----------------------------------------------------------------------
#  產生邀請連結 (Create / Send Invitation)
# ----------------------------------------------------------------------
@router.post(
    "/invite",
    response_model=SendInvitationResponse,
    summary="產生邀請連結",
    description="產生一組邀請連結與邀請碼，回傳的 invite_url 由前端呼叫 liff.shareTargetPicker 傳送給其他 LINE 使用者。",
)
async def create_invite(
    req: SendInvitationRequest,
    service: FamilyTreeService = Depends(get_family_tree_service)
):
    """
    產生一組邀請連結與邀請碼。
    回傳的 invite_url 由前端呼叫 liff.shareTargetPicker 傳送給其他 LINE 使用者。
    """
    return await service.send_invitation(req.inviter_id)

# ----------------------------------------------------------------------
#  接受加入族譜 (Accept Invitation)
# ----------------------------------------------------------------------
@router.post(
    "/accept",
    response_model=AddToFamilyResponse,
    summary="接受加入族譜",
    description="受邀者點擊邀請連結後，呼叫此 API 以雙向加入族譜。",
)
async def accept_invite(
    req: AddToFamilyRequest,
    service: FamilyTreeService = Depends(get_family_tree_service)
):
    """
    受邀者點擊邀請連結後，呼叫此 API 以雙向加入族譜。
    """
    return await service.add_to_family(
        invitee_id=req.invitee_id, 
        invite_id=req.invite_id
    )

# ----------------------------------------------------------------------
#  設定指定成員的關係 (Set Relationship)
# ----------------------------------------------------------------------
@router.post(
    "/relationship",
    response_model=FamilyTree,
    summary="設定指定成員的關係",
    description="設定發出請求的 user 與其族譜內特定 member_id 之間的關係，Service 層會自動嘗試雙向更新。",
)
async def set_relationship(
    req: SetRelationshipRequest,
    service: FamilyTreeService = Depends(get_family_tree_service)
):
    """
    設定發出請求的 user 與其族譜內特定 member_id 之間的關係。
    Service 層會自動根據反向對照表，嘗試雙向更新。
    """
    return await service.set_relationship(
        user_id=req.user_id,
        member_id=req.member_id,
        relationship_type=req.relationship_type
    )

