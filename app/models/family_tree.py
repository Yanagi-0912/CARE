from pydantic import BaseModel, Field, field_validator
from typing import Dict, List, Literal, Optional
from datetime import datetime, timezone

from app.models.family_authorization import (
    ASSIGNABLE_FAMILY_ROLES,
    DEFAULT_FAMILY_ROLE,
    DEFAULT_MIGRATION_STATE,
    FamilyRole,
    MigrationState,
)

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
    """家庭成員。

    這份文件屬於某一位資料擁有者，因此 `family_role` 的語意是「**這位成員對
    這份文件的擁有者的資料**具有什麼角色」——不是「擁有者對他」。方向很容易
    讀反，任何新增的呼叫端都要先確認自己要的是哪一邊（`GET /api/family/me`
    因此同時回報兩個方向，見 design 決策 5）。
    """

    user_id: str
    relationship_type: Optional[str] = None  # 加入後由使用者在 UI 設定
    display_name: Optional[str] = None       # 額外擴充：LINE 姓名（方案 A）
    picture_url: Optional[str] = None        # 額外擴充：LINE 頭像（方案 A）
    # 照顧對象標記是**業務狀態**，SHALL NOT 參與任何授權判定，也 SHALL NOT
    # 與 family_role 互相推導。它是單方面標記的——我在自己的族譜裡標記你是
    # 照顧對象，你不知情也不同意——任何從它推導出的權限都是自助式授權。
    is_care_recipient: bool = False
    # 家庭角色。`None` 代表**未設定**，授權上視為 MEMBER（見
    # DEFAULT_FAMILY_ROLE），但在「擁有者是否已完成引導式指派」的判定上算
    # 未完成。兩者在授權上等價、在指派上不等價，這個區分由欄位的有無承載，
    # 不需要額外欄位。
    #
    # 既有文件沒有這個欄位，讀回時為 None，行為與過去一致——不需要 backfill。
    family_role: Optional[FamilyRole] = None

    @field_validator("family_role")
    @classmethod
    def reject_owner(cls, value: Optional[str]) -> Optional[str]:
        """`OWNER` SHALL NOT 被指派。

        它是「這份資料是誰的」這個事實，由 `operator == target` 推導；允許
        寫入等於允許把資料的所有權讓渡出去，那是完全不同的一件事。擁有者
        無法操作時要走的是委任，不是改這個欄位。
        """
        if value is None:
            return None
        if value not in ASSIGNABLE_FAMILY_ROLES:
            raise ValueError(
                f"不可指派的家庭角色：{value}。"
                f"可用值：{sorted(ASSIGNABLE_FAMILY_ROLES)}"
            )
        return value

    @property
    def effective_family_role(self) -> FamilyRole:
        """授權判定用的角色；未設定時為 MEMBER。"""
        return self.family_role or DEFAULT_FAMILY_ROLE




class FamilyTree(BaseModel):
    """一位使用者的完整族譜。

    這份文件同時是該使用者作為**資料擁有者**的授權邊界：`family_members` 裡
    每個人的 `family_role`，決定他能對這位擁有者的資料做什麼。要提升某人對
    這位擁有者的權限，就必須寫入這份文件——而這份文件只有擁有者本人（或
    通過核可的受委任者）能寫。提權因此在資料路徑上就不可能，不必倚賴端點的
    if 判斷全部寫對。
    """

    user_id: str
    family_members: List[FamilyMember] = []
    # 這位擁有者的 RBAC 遷移狀態。強制以**擁有者**為邊界逐一啟用，不是單一
    # 全域切換——全域切換的那一刻，所有尚未指派角色的擁有者，其家人會同時
    # 失去功能，而尚未指派正是預設狀態。
    #
    # 判定時讀的是**目標擁有者**的狀態，不是操作者的：同一個人可能同時是甲
    # 家庭的照顧者與乙家庭的成員，甲已完成指派、乙還沒，那他對甲的資料就該
    # 受矩陣約束，對乙的不該。
    rbac_migration_state: MigrationState = DEFAULT_MIGRATION_STATE
    created_at: datetime
    updated_at: datetime


class PendingInvitation(BaseModel):
    """一筆待處理的族譜邀請。

    `owner_id` 與 `family_role` 是**伺服器端**保存的：邀請連結可以被轉發，
    角色若由接受方在請求裡攜帶，取得連結的人就能自選角色。接受邀請的路徑
    SHALL 忽略任何客戶端帶來的角色，一律以這裡存的為準。
    """

    id: str = Field(alias="_id")  # 隨機 token，作為邀請連結
    inviter_id: str  # 發送邀請的 LINE userId
    # 受邀者要加入誰的照護圈。省略時即邀請者本人——他對自己的資料是 OWNER，
    # 不需要任何額外授權。指向他人時，建立邀請者必須持有該擁有者的有效委任。
    owner_id: Optional[str] = None
    # 受邀者加入後的角色。None 代表未指定，加入時以 MEMBER 處理。
    family_role: Optional[FamilyRole] = None
    status: str = "pending"  # "pending" | "accepted" | "expired"
    created_at: datetime
    expires_at: datetime  # 建立後 7 天
    inviter_display_name: Optional[str] = None  # 關聯查詢填充的邀請者姓名

    @property
    def target_owner_id(self) -> str:
        """受邀者實際會加入誰的族譜。舊資料沒有 owner_id，即邀請者本人。"""
        return self.owner_id or self.inviter_id


class FamilyMemberWithPermissions(FamilyMember):
    """`GET /api/family/me` 的成員形狀：兩個方向的角色一起回。

    **兩者方向相反，很容易讀錯：**

    - `family_role`：**他對我的資料**是什麼角色。存在我的族譜文件裡，我是
      擁有者，我可以改這個值。
    - `my_role`：**我對他的資料**是什麼角色。存在他的族譜文件裡，由他決定，
      我不能改。

    `my_permissions` 是 `my_role` 攤平後、**已套用對方遷移狀態**的結果——也就
    是實際生效的權限，不是矩陣的理論值。前端據此渲染即可，SHALL NOT 自行
    判斷遷移狀態或套用矩陣：那等於在前端重建一次授權判定，而它必然會與後端
    漂移。這份資訊 SHALL NOT 構成授權，每支端點仍各自判定。
    """

    my_role: Optional[FamilyRole] = None
    my_permissions: Dict[str, List[str]] = Field(
        default_factory=lambda: {"general": [], "sensitive": [], "private": []}
    )
    # 對方的遷移狀態。前端不需要拿它做判斷（權限已經套用過了），但呈現面
    # 可能要據此說明「這位家人的家庭尚未啟用權限管理」。
    rbac_migration_state: MigrationState = DEFAULT_MIGRATION_STATE


class FamilyTreeWithPermissions(FamilyTree):
    family_members: List[FamilyMemberWithPermissions] = []


class FamilyRoleAssignmentStatus(BaseModel):
    """引導式角色指派的完成狀態。由後端依族譜資料判定，不採信前端旗標。"""

    owner_id: str
    is_complete: bool
    unassigned_member_ids: List[str] = []
    rbac_migration_state: MigrationState = DEFAULT_MIGRATION_STATE


# ── 注意定義順序 ──────────────────────────────────────────────────
# 這個 class 必須留在 `GetFamilyTreeResponse` **之前**。本檔沒有
# `from __future__ import annotations`，因此在 Python 3.13 以下，型別註解會在
# class 建立的當下就求值，前向參照直接 NameError；3.14 起 PEP 649 把註解改成
# 延後求值，同一份程式碼卻能過。本專案 CI 跑 3.12，開發機可能是 3.14——順序
# 錯了會在本機全綠、進 CI 才炸，而且錯誤訊息指向的是一堆不相干的測試檔。


class GetFamilyTreeResponse(BaseModel):
    family_tree: FamilyTreeWithPermissions
    # 我自己的引導式角色指派狀態。放在族譜回應裡而不是另開一支端點：族譜頁
    # 一載入就要知道「還有幾個人沒設定」，多一次往返沒有必要。
    role_assignment: Optional[FamilyRoleAssignmentStatus] = None


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


class SetCareRecipientRequest(BaseModel):
    member_id: str  # 要設定照顧對象標籤的成員 LINE userId
    is_care_recipient: bool = True


class SetFamilyRoleRequest(BaseModel):
    """指派家庭角色。

    型別刻意是寬鬆的 `str` 而非 `FamilyRole`：spec 要求指派 `OWNER` 時回
    **400**，而 Pydantic 的型別檢查會在進到處理函式之前就回 422。把值的檢查
    留給服務層，狀態碼才對得上 spec，錯誤訊息也才講得出「OWNER 不是可指派的
    角色」而不是一句 literal 不匹配。
    """

    family_role: str


class CreateInviteRequest(BaseModel):
    """建立邀請。兩個欄位皆可省略。

    `owner_id` 指向他人時需要該擁有者的有效委任；`family_role` 為 `GUARDIAN`
    時僅擁有者本人可指定——受委任者建立的邀請限 `CAREGIVER`／`MEMBER`。
    """

    owner_id: Optional[str] = None
    family_role: Optional[str] = None


class FamilyRoleEntry(BaseModel):
    """`GET /api/family/owners/{ownerId}/members/roles` 的單筆回應。

    `family_role` 為 None 時代表**未設定**——呈現面要據此告訴擁有者「這個人
    目前會以 MEMBER 的權限處理」，SHALL NOT 直接顯示成 MEMBER 而讓擁有者
    以為自己已經設定過了。
    """

    user_id: str
    display_name: Optional[str] = None
    family_role: Optional[FamilyRole] = None
    effective_family_role: FamilyRole = DEFAULT_FAMILY_ROLE


# 受委任 GUARDIAN 的預設效期。
#
# 委任是在擁有者無法表達意願時建立的，因此它 SHALL NOT 在擁有者恢復意願之後
# 仍然自動延續——到期與可撤銷是同一個考量的兩面。沒有「不到期」這個選項。
DELEGATION_DEFAULT_VALID_DAYS = 90


class FamilyDelegation(BaseModel):
    """一筆受委任 GUARDIAN 的授權紀錄。

    這是本系統唯一一條「不經資料擁有者同意就取得其資料權限」的路徑，因此
    三個時間欄位一起決定它有沒有效：`revoked_at` 為空且尚未超過 `expires_at`
    才算有效。到期或撤銷 SHALL NOT 刪除這筆紀錄，也 SHALL NOT 動到該成員在
    族譜中的 `family_role`——失效只收回「代擁有者行事」這件事，而委任存續的
    那段期間正是最需要事後查得到的一段。
    """

    owner_id: str            # 資料擁有者
    delegate_user_id: str    # 受委任者
    granted_at: datetime
    granted_by: str          # 核可者／執行委任建立的一方
    expires_at: datetime
    revoked_at: Optional[datetime] = None
    revoked_by: Optional[str] = None
    # 指向核可流程（身分驗證、醫療證明、法定監護證明）的證據。其內容與查驗
    # 方式由後續的產品／法務 change 定義——欄位先留，格式不猜。
    approval_ref: Optional[str] = None

    def is_active_at(self, moment: datetime) -> bool:
        """在某個時刻是否有效。

        比較前一律補上 UTC 時區：Motor client 未啟用 tz_aware，pymongo 會把
        datetime 以 UTC 寫入、再以 naive UTC 讀回，直接與帶時區的 now 比較會
        拋 TypeError（沿用 app/models/medication.py 的 ensure_aware_utc 慣例）。
        """
        if self.revoked_at is not None:
            return False
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment < expires_at


class FamilyRoleAuditEntry(BaseModel):
    """角色或委任變更的稽核紀錄（僅可追加）。

    `via_delegation` 是必要的：事後要分得出「長輩自己指派的」與「別人代他
    指派的」，那是兩件性質完全不同的授權。
    """

    owner_id: str
    member_id: str
    from_role: Optional[str] = None
    to_role: Optional[str] = None
    changed_at: datetime
    changed_by: str
    via_delegation: bool = False
    # 角色指派以外的事件（委任建立／撤銷／到期）也走同一份稽核，用這個欄位
    # 區分，避免為了兩三種事件各開一個 collection 而讓時序拼不回來。
    event: Literal["role_change", "delegation_granted", "delegation_revoked"] = (
        "role_change"
    )


