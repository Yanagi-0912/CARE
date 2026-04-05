from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from datetime import datetime


# 關係類型的反向對照表：User A 設定 → User B 自動取得的反向關係
REVERSE_RELATIONSHIP: Dict[str, str] = {
    "parent":      "child",
    "child":       "parent",
    "spouse":      "spouse",
    "sibling":     "sibling",
    "grandparent": "grandchild",
    "grandchild":  "grandparent",
    "other":       "other",
}



class FamilyMember(BaseModel):
    """族譜中的一位成員，必須是擁有 LINE UID 的真實用戶"""
    user_id: str
    relationship_type: Optional[str] = None  # 加入後由使用者在 UI 設定


class FamilyTree(BaseModel):
    """一位使用者的完整族譜"""
    user_id: str
    family_members: List[FamilyMember] = []
    created_at: datetime
    updated_at: datetime


class PendingInvitation(BaseModel):
    """一筆待處理的族譜邀請"""
    id: str = Field(alias="_id")       # 8 碼隨機 short ID，作為邀請連結的 token
    inviter_id: str                    # 發送邀請的 LINE userId
    status: str = "pending"            # "pending" | "accepted" | "expired"
    created_at: datetime
    expires_at: datetime               # 建立後 7 天


# ── API Request / Response Schemas ──────────────────────────────────────────

class SendInvitationRequest(BaseModel):
    inviter_id: str


class SendInvitationResponse(BaseModel):
    invite_id: str
    invite_url: str                    # 組合好的 LIFF 邀請連結


class AddToFamilyRequest(BaseModel):
    invitee_id: str
    invite_id: str


class AddToFamilyResponse(BaseModel):
    success: bool
    family_tree: FamilyTree


class SetRelationshipRequest(BaseModel):
    user_id: str
    member_id: str                     # 要設定關係的成員的 LINE userId
    relationship_type: str             # "parent" | "child" | "spouse" | "sibling" | "other"


class GetFamilyTreeResponse(BaseModel):
    family_tree: FamilyTree
