from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Literal
from datetime import datetime

# 關係類型的反向對照表 User A 設定 → User B 自動取得的反向關係
REVERSE_RELATIONSHIP: Dict[str, str] = {
    "parent": "child",
    "child": "parent",
    "spouse": "spouse",
    "sibling": "sibling",
    "grandparent": "grandchild",
    "grandchild": "grandparent",
    "other": "other",
}


class FamilyMember(BaseModel):
    """家庭成員"""

    user_id: str
    relationship_type: Optional[str] = None  # 加入後由使用者在 UI 設定
    display_name: Optional[str] = None       # 額外擴充：LINE 姓名（方案 A）
    picture_url: Optional[str] = None        # 額外擴充：LINE 頭像（方案 A）


class FamilyTree(BaseModel):
    """一位使用者的完整族譜"""

    user_id: str
    family_members: List[FamilyMember] = []
    created_at: datetime
    updated_at: datetime


class PendingInvitation(BaseModel):
    """一筆待處理的族譜邀請"""

    id: str = Field(alias="_id")  # 隨機 token，作為邀請連結
    inviter_id: str  # 發送邀請的 LINE userId
    status: str = "pending"  # "pending" | "accepted" | "expired"
    created_at: datetime
    expires_at: datetime  # 建立後 7 天
    inviter_display_name: Optional[str] = None  # 關聯查詢填充的邀請者姓名


class GetFamilyTreeResponse(BaseModel):
    family_tree: FamilyTree


class CreateInviteResponse(BaseModel):
    invite_token: str
    expires_at: str  # ISO 8601


class VerifyInviteResponse(BaseModel):
    inviter_display_name: str
    expires_at: str


class AcceptInviteRequest(BaseModel):
    code: str


class AcceptInviteResponse(BaseModel):
    status: Literal["joined", "already_member"]
    message: Optional[str] = None


class SetRelationshipRequest(BaseModel):
    member_id: str  # 要設定關係的成員的 LINE userId
    relationship_type: str  # "parent" | "child" | "spouse" | "sibling" | "other"
