from fastapi import APIRouter, Depends, HTTPException

from typing import List, Optional

from app.models.family_tree import (
    CreateInviteRequest,
    FamilyMemberWithPermissions,
    FamilyTreeWithPermissions,
    FamilyRoleAssignmentStatus,
    FamilyRoleEntry,
    FamilyTree,
    GetFamilyTreeResponse,
    CreateInviteResponse,
    VerifyInviteResponse,
    AcceptInviteRequest,
    AcceptInviteResponse,
    SetFamilyRoleRequest,
    SetRelationshipRequest,
    SetCareRecipientRequest,
)
from app.services.family.family_authorization_service import (
    FamilyAuthorizationService,
)
from app.services.family.family_delegation_service import (
    FamilyDelegationService,
)
from app.services.family.family_role_service import FamilyRoleService
from app.services.family.family_tree_service import FamilyTreeService
from app.dependencies import (
    get_family_authorization_service,
    get_family_delegation_service,
    get_family_role_service,
    get_family_tree_service,
    get_current_user,
    CurrentUser,
)

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
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
    role_service: FamilyRoleService = Depends(get_family_role_service),
):
    """取得自己的族譜，並附上兩個方向的角色與**實際生效**的權限。

    方向很容易讀反，所以兩個都給：`family_role` 是「他對我的資料」的角色
    （我可以改），`my_role` 是「我對他的資料」的角色（他決定，我不能改）。
    `my_permissions` 已套用對方的遷移狀態，前端照著渲染即可，不必也不應該
    自行判斷狀態或套用矩陣。

    權限資訊 SHALL NOT 構成授權——每支端點仍各自判定。前端拿到的東西永遠
    可能是舊的，那時後端的 403 才是真正的邊界。
    """
    operator_id = current_user.line_user_id
    tree = await service.get_family_tree(operator_id)

    member_ids = [m.user_id for m in tree.family_members]
    described = await authz.describe_members(operator_id, member_ids)

    members = [
        FamilyMemberWithPermissions(
            **m.model_dump(),
            my_role=described.get(m.user_id, {}).get("my_role"),
            my_permissions=described.get(m.user_id, {}).get(
                "my_permissions",
                {"general": [], "sensitive": [], "private": []},
            ),
            rbac_migration_state=described.get(m.user_id, {}).get(
                "rbac_migration_state", "shadow"
            ),
        )
        for m in tree.family_members
    ]

    enriched = FamilyTreeWithPermissions(
        **{**tree.model_dump(), "family_members": [m.model_dump() for m in members]}
    )
    role_assignment = await role_service.assignment_status(operator_id, operator_id)
    return GetFamilyTreeResponse(
        family_tree=enriched, role_assignment=role_assignment
    )

@router.post(
    "/invites",
    response_model=CreateInviteResponse,
    summary="產生邀請碼",
    description="根據目前登入的使用者產生一個隨機邀請碼。",
)
async def create_invite(
    req: Optional[CreateInviteRequest] = None,
    current_user: CurrentUser = Depends(get_current_user),
    service: FamilyTreeService = Depends(get_family_tree_service),
    authz: FamilyAuthorizationService = Depends(get_family_authorization_service),
):
    """建立邀請。可指定受邀者加入哪一位擁有者的照護圈與加入後的角色。

    請求主體可省略（維持既有呼叫端的相容性）：省略時等同「邀請加入我自己的
    照護圈、角色未指定」，行為與變更前完全相同。

    角色的資格判定全部在建立當下完成，並把結果存進邀請記錄——`accept` 一律
    忽略客戶端帶來的角色。
    """
    payload = req or CreateInviteRequest()
    invitation = await service.create_invitation(
        inviter_id=current_user.line_user_id,
        owner_id=payload.owner_id,
        family_role=payload.family_role,
        authorization_service=authz,
    )
    return CreateInviteResponse(
        invite_token=invitation.id, expires_at=invitation.expires_at.isoformat()
    )


@router.get(
    "/invites/verify/{code}",
    response_model=VerifyInviteResponse,
    summary="驗證邀請碼",
    description="驗證邀請碼是否有效。此為公開 API，不需要認證。",
)
async def verify_invite(
    code: str, service: FamilyTreeService = Depends(get_family_tree_service)
):
    invitation = await service.verify_invitation(code)
    return VerifyInviteResponse(
        inviter_display_name=invitation.inviter_display_name or "家人",
        expires_at=invitation.expires_at.isoformat(),
    )

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
    status, message = await service.accept_invitation(
        invitee_id=current_user.line_user_id, code=req.code
    )
    return AcceptInviteResponse(status=status, message=message)

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
    "/care-recipient",
    response_model=FamilyTree,
    summary="設定照顧對象標籤",
    description="設定目前使用者族譜內特定成員是否為照顧對象 (is_care_recipient)。",
)
async def set_care_recipient(
    req: SetCareRecipientRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: FamilyTreeService = Depends(get_family_tree_service),
):
    return await service.set_care_recipient(
        user_id=current_user.line_user_id,
        member_id=req.member_id,
        is_care_recipient=req.is_care_recipient,
    )



# ── 角色管理 ──────────────────────────────────────────────────────────────
#
# 兩組路徑對應同一個處理函式：省略 ownerId 時即呼叫者本人的照護圈。做成兩條
# 路由而不是把 ownerId 設成可選的路徑參數，是因為 FastAPI 的路徑參數無法在
# 中段可選；而「省略即自己」這個語意值得保留——絕大多數呼叫是擁有者管理自己
# 的家庭，不該逼他們在 URL 裡重複自己的 id。


@router.put(
    "/members/{memberId}/role",
    response_model=FamilyTree,
    summary="指派家庭成員角色（自己的照護圈）",
    description="設定自己族譜中特定成員的家庭角色。僅資料擁有者本人或其受委任者可呼叫。",
)
async def set_own_member_role(
    memberId: str,
    req: SetFamilyRoleRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: FamilyRoleService = Depends(get_family_role_service),
):
    return await service.assign_role(
        operator_id=current_user.line_user_id,
        owner_id=current_user.line_user_id,
        member_id=memberId,
        family_role=req.family_role,
    )


@router.put(
    "/owners/{ownerId}/members/{memberId}/role",
    response_model=FamilyTree,
    summary="指派家庭成員角色（代其他擁有者）",
    description=(
        "設定指定擁有者族譜中特定成員的家庭角色。"
        "呼叫者非擁有者本人時，必須持有該擁有者的有效委任；"
        "受委任者不得授予 GUARDIAN。"
    ),
)
async def set_member_role(
    ownerId: str,
    memberId: str,
    req: SetFamilyRoleRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: FamilyRoleService = Depends(get_family_role_service),
):
    return await service.assign_role(
        operator_id=current_user.line_user_id,
        owner_id=ownerId,
        member_id=memberId,
        family_role=req.family_role,
    )


@router.get(
    "/members/roles",
    response_model=List[FamilyRoleEntry],
    summary="查詢自己照護圈中每位成員的角色",
    description=(
        "供擁有者的角色管理介面使用。`family_role` 為 null 代表尚未設定，"
        "該成員目前以 MEMBER 的權限處理。"
    ),
)
async def list_own_member_roles(
    current_user: CurrentUser = Depends(get_current_user),
    service: FamilyRoleService = Depends(get_family_role_service),
):
    return await service.list_roles(
        operator_id=current_user.line_user_id,
        owner_id=current_user.line_user_id,
    )


@router.get(
    "/owners/{ownerId}/members/roles",
    response_model=List[FamilyRoleEntry],
    summary="查詢指定擁有者照護圈中每位成員的角色",
)
async def list_member_roles(
    ownerId: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: FamilyRoleService = Depends(get_family_role_service),
):
    return await service.list_roles(
        operator_id=current_user.line_user_id, owner_id=ownerId
    )


@router.get(
    "/role-assignment-status",
    response_model=FamilyRoleAssignmentStatus,
    summary="查詢引導式角色指派的完成狀態",
    description=(
        "完成與否由後端依族譜資料判定：每一位現有成員都要持有明確可解析的"
        " family_role。未設定者會列在 unassigned_member_ids，介面需明確告知"
        "擁有者這些人將以 MEMBER 的權限處理。"
    ),
)
async def get_role_assignment_status(
    current_user: CurrentUser = Depends(get_current_user),
    service: FamilyRoleService = Depends(get_family_role_service),
):
    return await service.assignment_status(
        operator_id=current_user.line_user_id,
        owner_id=current_user.line_user_id,
    )


# ── 委任 ──────────────────────────────────────────────────────────────────


@router.delete(
    "/delegations/{delegateUserId}",
    summary="撤銷委任",
    description=(
        "撤銷自己資料上對某位受委任者的全部有效委任。僅資料擁有者本人可呼叫；"
        "撤銷立即生效，且不受委任啟用開關限制——閘門管的是能不能給出去，"
        "不是能不能收回來。"
    ),
)
async def revoke_delegation(
    delegateUserId: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: FamilyDelegationService = Depends(get_family_delegation_service),
):
    revoked = await service.revoke(
        operator_id=current_user.line_user_id,
        owner_id=current_user.line_user_id,
        delegate_user_id=delegateUserId,
    )
    return {"revoked": revoked}
