"""家庭授權的唯一決策點。

**所有**跨使用者的存取都要經過這裡。router 與其他 service SHALL NOT 自行判斷
「他是不是家人」「他是什麼角色」「這份資料算哪一級」——那正是本 change 要
消滅的東西：`ensure_family_member` 的語意（在族譜裡＝有權）散在三支端點與
三處手寫的 `any(m.user_id == ...)` 裡，每一處都是一個可能寫錯的地方。

判定由五項輸入決定，缺一不可：

1. 操作者（operator_id）
2. 目標資料的擁有者（target_owner_id）
3. 該擁有者族譜中操作者的角色（含委任解析）
4. 資料分類（GENERAL／SENSITIVE／PRIVATE）
5. 動作（READ／WRITE）

**端點的請求裡帶得出目標使用者識別，SHALL NOT 構成任何允許的依據。** 帶得出
目標只代表知道要動誰的資料，不代表可以動——那是路徑參數最容易被當成授權的
地方，也是這個模組存在的理由。
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from app.models.family_authorization import (
    Action,
    DataClassification,
    DEFAULT_FAMILY_ROLE,
    DEFAULT_MIGRATION_STATE,
    FIELD_CLASSIFICATION,
    FamilyRole,
    MigrationState,
    NotificationKind,
    ResourceName,
    is_allowed,
    notification_recipient_roles,
)
from app.models.family_tree import FamilyRoleAssignmentStatus, FamilyTree

logger = logging.getLogger(__name__)

# 巢狀資源：某個欄位的內容本身是另一種資源，遮蔽時要遞迴進去。
#
# 只有一筆，但一定要列出來——`GET /reminders` 回傳的提醒規則裡就藏著整份
# 藥品清單，而適應症是 SENSITIVE。少了這條，外層遮蔽會放行整個
# `medications` 欄位（它自己登記為 GENERAL），適應症就從巢狀結構裡漏出去。
NESTED_RESOURCES: dict[tuple[ResourceName, str], ResourceName] = {
    ("medication_reminder", "medications"): "medication",
}


class FamilyAuthorizationService:
    """家庭授權判定。

    刻意**不做成 FastAPI dependency**：分類與動作取決於端點要動的是什麼資料，
    做成 dependency 會逼出一堆 `require_sensitive_read` 之類的參數化工廠，
    讓「哪個端點檢查了什麼」散在裝飾器裡看不清楚。改為在 router 或 service 內
    顯式呼叫一行 `await authz.authorize(...)`，讀程式碼時檢查點就在眼前。
    """

    def __init__(
        self,
        family_tree_repository: Any,
        delegation_repository: Any,
        enforcement_enabled: bool = False,
        metrics_repository: Any = None,
    ) -> None:
        self._trees = family_tree_repository
        self._delegations = delegation_repository
        # 遷移指標的計數器。選填：未注入時只記 log，授權行為完全不變。
        # 它是觀測工具，SHALL NOT 成為授權路徑的失敗點。
        self._metrics = metrics_repository
        # 全域總閘（kill switch）。關閉時一律不強制，不論各擁有者的狀態為何——
        # 出事時要有一個地方能讓全體立刻回到變更前的行為，而不必逐一改資料。
        self._enforcement_enabled = enforcement_enabled

    # ── 狀態與角色解析 ────────────────────────────────────────────────

    async def _get_tree(self, owner_id: str) -> Optional[FamilyTree]:
        return await self._trees.get_by_user_id(owner_id)

    async def migration_state(self, target_owner_id: str) -> MigrationState:
        """某位擁有者當下的遷移狀態。

        讀的是**目標擁有者**的狀態，不是操作者的：同一個人可能同時是甲家庭的
        照顧者與乙家庭的成員，甲已完成指派、乙還沒，那他對甲的資料就該受矩陣
        約束，對乙的不該。要保護的是資料，不是使用者。
        """
        if not self._enforcement_enabled:
            return "shadow"
        tree = await self._get_tree(target_owner_id)
        if tree is None:
            return DEFAULT_MIGRATION_STATE
        return tree.rbac_migration_state

    async def resolve_role(
        self,
        operator_id: str,
        target_owner_id: str,
        now: Optional[datetime] = None,
    ) -> Optional[FamilyRole]:
        """解析操作者對某位擁有者的角色；不在其族譜內時回 None。

        角色是 (操作者, 目標擁有者) 這組**配對**的性質，不是操作者自身的屬性：
        同一個人對甲是 GUARDIAN、對乙可以是 MEMBER。

        解析順序：

        1. `operator == target` → `OWNER`。這一步不查資料庫——一個人對自己
           資料的權限不需要任何人授予，也不該因為族譜讀取失敗而消失。
        2. 不在目標擁有者的族譜內 → `None`（不是家人就沒有任何權限）。
        3. 持有**有效**委任 → `GUARDIAN`。已到期或已撤銷者不在此列，因為
           `has_active_delegation` 在查詢時就篩掉了。委任 SHALL NOT 解析為
           `OWNER`——擁有權不轉移。
        4. 其餘 → 族譜中登記的 `family_role`，缺席視為 `MEMBER`。
        """
        role, _ = await self._resolve_context(operator_id, target_owner_id, now=now)
        return role

    async def _resolve_context(
        self,
        operator_id: str,
        target_owner_id: str,
        now: Optional[datetime] = None,
    ) -> tuple[Optional[FamilyRole], bool]:
        """一次讀取算出角色與 legacy 判定（在不在族譜裡）。

        兩者都要用到同一份族譜文件。分成兩支各查一次，每個 authorize 就是
        兩趟往返——那是授權的熱路徑，每支端點都會走。
        """
        if operator_id == target_owner_id:
            return "OWNER", True

        tree = await self._get_tree(target_owner_id)
        if tree is None:
            return None, False

        member = next(
            (m for m in tree.family_members if m.user_id == operator_id), None
        )
        if member is None:
            return None, False

        moment = now or datetime.now(tz=timezone.utc)
        if await self._delegations.has_active_delegation(
            owner_id=target_owner_id, delegate_user_id=operator_id, now=moment
        ):
            # 委任給的是 GUARDIAN 的**資料**權限，不多不少。額外能力（代為
            # 指派角色、代為建立邀請）由呼叫端另外向 is_active_delegate 詢問，
            # 不從這個回傳值推導——否則「受委任的 GUARDIAN」與「擁有者親自
            # 指派的 GUARDIAN」在這裡就分不出來了，而前者不得授予 GUARDIAN。
            return "GUARDIAN", True

        return member.family_role or DEFAULT_FAMILY_ROLE, True

    async def is_active_delegate(
        self,
        operator_id: str,
        target_owner_id: str,
        now: Optional[datetime] = None,
    ) -> bool:
        """操作者是否對該擁有者持有有效委任。

        與 `resolve_role` 分開，因為兩者回答不同問題：那支回答「能讀寫什麼」，
        這支回答「能不能代擁有者行事」。受委任者與擁有者親自指派的 GUARDIAN
        在資料權限上完全相同，但只有前者能代為指派角色、也只有後者能授予
        GUARDIAN——混用會直接產生一條提權路徑。
        """
        if operator_id == target_owner_id:
            return False
        moment = now or datetime.now(tz=timezone.utc)
        return await self._delegations.has_active_delegation(
            owner_id=target_owner_id, delegate_user_id=operator_id, now=moment
        )

    async def _is_family_member(self, operator_id: str, target_owner_id: str) -> bool:
        """legacy 判定：在不在目標擁有者的族譜裡。

        這是 `ensure_family_member` 的語意，保留下來**只為了影子模式的比對**，
        SHALL NOT 被任何新的授權路徑當作依據。
        """
        if operator_id == target_owner_id:
            return True
        tree = await self._get_tree(target_owner_id)
        if tree is None:
            return False
        return any(m.user_id == operator_id for m in tree.family_members)

    # ── 判定 ──────────────────────────────────────────────────────────

    async def can(
        self,
        operator_id: str,
        target_owner_id: str,
        classification: DataClassification,
        action: Action,
        now: Optional[datetime] = None,
    ) -> bool:
        """純 RBAC 判定，不含遷移狀態，也不拋例外。

        呈現面（`describe`）與通知政策以外的地方不該直接用它——會漏掉影子
        模式。要判斷「這次請求能不能過」請用 `authorize`。
        """
        role = await self.resolve_role(operator_id, target_owner_id, now=now)
        return is_allowed(role, classification, action)

    async def authorize(
        self,
        operator_id: str,
        target_owner_id: str,
        classification: DataClassification,
        action: Action,
        has_legacy_equivalent: bool = True,
        now: Optional[datetime] = None,
    ) -> FamilyRole | None:
        """判定並在不通過時拋出 403。回傳解析到的角色，供呼叫端做欄位遮蔽。

        `has_legacy_equivalent` 指這個存取路徑在本能力導入**前**是否存在。

        - `True`（預設）：既有端點。影子模式下依 legacy 判定放行，行為與變更
          前完全相同。
        - `False`：本 change 新增的路徑（例如健康資料的代理寫入）。這種路徑
          在導入前根本不存在，「與導入前相同」的意思是**沒有這個能力**，因此
          一律以 RBAC 判定，不受影子模式放寬。少了這個區分，影子模式會讓
          一位 MEMBER 在遷移期間取得他在強制後反而沒有的寫入權——那不是保留
          既有行為，那是憑空發明一個更寬的行為。
        """
        moment = now or datetime.now(tz=timezone.utc)
        role, legacy_allowed = await self._resolve_context(
            operator_id, target_owner_id, now=moment
        )
        rbac_allowed = is_allowed(role, classification, action)

        if not has_legacy_equivalent:
            if not rbac_allowed:
                raise self._forbidden(classification, action)
            return role

        state = await self.migration_state(target_owner_id)

        await self._count_decision(target_owner_id)
        if legacy_allowed != rbac_allowed:
            await self._count_diff(
                target_owner_id, "tighten" if legacy_allowed else "loosen"
            )
            self._record_migration_diff(
                operator_id=operator_id,
                target_owner_id=target_owner_id,
                classification=classification,
                action=action,
                role=role,
                legacy_allowed=legacy_allowed,
                rbac_allowed=rbac_allowed,
                state=state,
            )

        effective = rbac_allowed if state == "enforced" else legacy_allowed
        if not effective:
            raise self._forbidden(classification, action)
        return role

    @staticmethod
    def _forbidden(classification: DataClassification, action: Action) -> HTTPException:
        """權限不足一律 403，且訊息要說是權限不足、不是查無資料。

        兩者在介面上要給使用者完全不同的話——「你沒有權限看這個」與「他還沒
        填這份資料」。混用同一個狀態碼，前端就寫不出正確的文案，只能一律
        顯示「載入失敗」。
        """
        return HTTPException(
            status_code=403,
            detail=f"權限不足：您對此使用者的{classification}資料沒有{action}權限",
        )

    def _record_migration_diff(
        self,
        operator_id: str,
        target_owner_id: str,
        classification: DataClassification,
        action: Action,
        role: Optional[FamilyRole],
        legacy_allowed: bool,
        rbac_allowed: bool,
        state: MigrationState,
    ) -> None:
        """記錄 legacy 與 RBAC 兩種判定的差異。只記判定要素，不記任何資料內容。

        兩個方向分開，是因為它們的意義完全不同：

        - **收緊**（legacy 允許、RBAC 拒絕）：遷移的成本，數量決定切換時機。
        - **放寬**（legacy 拒絕、RBAC 允許）：不該存在。RBAC 允許了 legacy 不
          允許的事，代表角色解析或矩陣有錯。它是 bug 訊號，不是遷移資訊，因此
          用較高的層級記錄——混進同一批統計數字裡，這個訊號會被遷移噪音淹沒。
        """
        direction = "tighten" if legacy_allowed else "loosen"
        payload = {
            "event": "family_rbac_migration_diff",
            "direction": direction,
            "operator_id": operator_id,
            "target_owner_id": target_owner_id,
            "classification": classification,
            "action": action,
            "resolved_role": role,
            "legacy_allowed": legacy_allowed,
            "rbac_allowed": rbac_allowed,
            "migration_state": state,
        }
        if direction == "loosen":
            logger.error("RBAC 判定比 legacy 寬鬆，這是 bug 訊號：%s", payload)
        else:
            logger.info("RBAC 遷移差異（收緊）：%s", payload)

    async def _count_decision(self, owner_id: str) -> None:
        """累加一次判定，作為收緊差異比例的分母。

        指標失敗 SHALL NOT 讓授權失敗：它是拿來決定何時切換的觀測資料，不是
        安全邊界。一個計數器寫不進去就擋掉使用者的請求，是把觀測工具變成
        單點故障。
        """
        if self._metrics is None:
            return
        try:
            await self._metrics.record_decision(owner_id)
        except Exception as exc:  # noqa: BLE001 - 觀測旁路，例外不得逸散
            logger.warning("遷移指標寫入失敗（分母）：%s", type(exc).__name__)

    async def _count_diff(self, owner_id: str, direction: str) -> None:
        """累加一次差異。同上，失敗一律吞掉。"""
        if self._metrics is None:
            return
        try:
            await self._metrics.record(owner_id, direction)
        except Exception as exc:  # noqa: BLE001
            logger.warning("遷移指標寫入失敗（%s）：%s", direction, type(exc).__name__)

    # ── 欄位遮蔽（fail-closed）────────────────────────────────────────

    def visible_fields(
        self, role: Optional[FamilyRole], resource: ResourceName
    ) -> frozenset[str]:
        """該角色在跨使用者讀取時看得到的欄位。

        **未登記的欄位一律不在其中。** 這是 fail-closed，而方向是重點：若未
        登記者落回資源的預設分類，任何人日後新增一個欄位就會在沒有錯誤、沒有
        提示的情況下對權限最低的成員公開。反過來，漏登記的後果是「家人畫面上
        那個欄位不見了」——會被發現、會被回報、不會外洩。
        """
        return frozenset(
            field
            for (registered_resource, field), classification in (
                FIELD_CLASSIFICATION.items()
            )
            if registered_resource == resource
            and is_allowed(role, classification, "READ")
        )

    def mask(
        self,
        payload: Any,
        resource: ResourceName,
        role: Optional[FamilyRole],
        is_self: bool = False,
    ) -> Any:
        """依角色遮蔽跨使用者回應的欄位。

        `is_self=True` 時原樣回傳：操作者讀自己的資料不經遮蔽，否則新增一個
        還沒登記的欄位會連本人都看不到自己的資料。

        接受 dict 或 list，因為呼叫端有時是單筆、有時是清單。巢狀資源
        （提醒規則裡的藥品清單）會遞迴處理——少了這一步，適應症會從巢狀結構
        裡漏出去，而外層看起來一切正常。
        """
        if is_self:
            return payload

        if isinstance(payload, list):
            return [self.mask(item, resource, role, is_self=False) for item in payload]

        if not isinstance(payload, dict):
            return payload

        allowed = self.visible_fields(role, resource)
        masked: Dict[str, Any] = {}
        for field, value in payload.items():
            if field not in allowed:
                continue
            nested = NESTED_RESOURCES.get((resource, field))
            if nested is not None:
                masked[field] = self.mask(value, nested, role, is_self=False)
            else:
                masked[field] = value
        return masked

    async def mask_response(
        self,
        payload: Any,
        resource: ResourceName,
        operator_id: str,
        target_owner_id: str,
        now: Optional[datetime] = None,
    ) -> Any:
        """依角色遮蔽跨使用者回應，且**尊重該擁有者的遷移狀態**。

        這一點容易漏掉：遮蔽也是一種收緊。影子模式承諾「行為與導入前完全
        相同」，而導入前沒有任何遮蔽——若在影子狀態下就把適應症拿掉，使用者
        會在沒有任何切換的情況下發現東西不見了，而那正是影子模式要避免的。

        因此遮蔽只在 `enforced` 生效；影子狀態下原樣回傳。與 `authorize` 的
        放行判定同一個依據，兩者不會各說各話。
        """
        if operator_id == target_owner_id:
            return payload

        state = await self.migration_state(target_owner_id)
        if state != "enforced":
            return payload

        role = await self.resolve_role(operator_id, target_owner_id, now=now)
        return self.mask(payload, resource, role, is_self=False)

    # ── 呈現面與通知 ──────────────────────────────────────────────────

    async def describe(
        self,
        operator_id: str,
        target_owner_ids: List[str],
        now: Optional[datetime] = None,
    ) -> Dict[str, Dict[str, List[str]]]:
        """操作者對每位目標**實際生效**的權限，供前端決定要不要渲染入口。

        回傳的是套用遷移狀態之後的結果，不是矩陣的理論值：處於影子狀態的
        擁有者，其成員拿到的權限描述與變更前一致。前端因此只需要回答「後端
        說我能不能」，不必知道「這個家庭切換了沒」——後者一旦要在前端重算，
        就是第二個安全邊界，而它必然會與第一個漂移。

        **這不是授權。** 每一支端點仍各自呼叫 `authorize`；前端拿到的東西
        永遠可能是舊的（TanStack Query 預設 staleTime 30 秒）。
        """
        moment = now or datetime.now(tz=timezone.utc)
        result: Dict[str, Dict[str, List[str]]] = {}
        for owner_id in target_owner_ids:
            role = await self.resolve_role(operator_id, owner_id, now=moment)
            state = await self.migration_state(owner_id)
            legacy_allowed = await self._is_family_member(operator_id, owner_id)
            entry: Dict[str, List[str]] = {}
            for classification in ("GENERAL", "SENSITIVE", "PRIVATE"):
                actions: List[str] = []
                for action in ("READ", "WRITE"):
                    if state == "enforced":
                        permitted = is_allowed(role, classification, action)
                    else:
                        permitted = legacy_allowed and self._legacy_permits(
                            classification, action
                        )
                    if permitted:
                        actions.append(action)
                entry[classification.lower()] = actions
            result[owner_id] = entry
        return result

    async def describe_members(
        self,
        operator_id: str,
        owner_ids: List[str],
        now: Optional[datetime] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """批次版的 `describe`，額外附上解析到的角色與該擁有者的遷移狀態。

        以兩次查詢完成（族譜一次、有效委任一次），SHALL NOT 對每位成員各發
        一次——族譜頁一次可能有十餘位成員，N+1 在長輩的行動網路上是看得見的
        延遲。

        回傳的權限是**實際生效**的值，已套用該擁有者的遷移狀態：處於影子狀態
        者，其權限描述與變更前一致。前端因此只需要回答「後端說我能不能」，
        不必知道「這個家庭切換了沒」——後者一旦在前端重算，就是第二個安全
        邊界，而它必然會與第一個漂移。
        """
        if not owner_ids:
            return {}
        moment = now or datetime.now(tz=timezone.utc)

        rows = await self._trees.get_roles_for_operator(operator_id, owner_ids)
        delegated_owner_ids = set(
            await self._delegations.list_delegated_owner_ids(
                operator_id, owner_ids, now=moment
            )
        )

        described: Dict[str, Dict[str, Any]] = {}
        for owner_id in owner_ids:
            row = rows.get(owner_id)
            if row is None:
                # 不在對方的族譜裡：沒有任何權限。SHALL NOT 給預設角色。
                described[owner_id] = {
                    "my_role": None,
                    "rbac_migration_state": "shadow",
                    "my_permissions": {"general": [], "sensitive": [], "private": []},
                }
                continue

            if owner_id in delegated_owner_ids:
                role: Optional[FamilyRole] = "GUARDIAN"
            else:
                role = row.get("family_role") or DEFAULT_FAMILY_ROLE

            state = (
                row.get("rbac_migration_state", DEFAULT_MIGRATION_STATE)
                if self._enforcement_enabled
                else "shadow"
            )

            permissions: Dict[str, List[str]] = {}
            for classification in ("GENERAL", "SENSITIVE", "PRIVATE"):
                actions = []
                for action in ("READ", "WRITE"):
                    if state == "enforced":
                        permitted = is_allowed(role, classification, action)
                    else:
                        permitted = self._legacy_permits(classification, action)
                    if permitted:
                        actions.append(action)
                permissions[classification.lower()] = actions

            described[owner_id] = {
                "my_role": role,
                "rbac_migration_state": state,
                "my_permissions": permissions,
            }
        return described

    @staticmethod
    def _legacy_permits(classification: str, action: str) -> bool:
        """變更前，一位族譜成員實際做得到的事。

        照抄舊行為，不是照抄矩陣：舊碼允許讀健康資料與對話紀錄、也允許讀寫
        用藥提醒，但**沒有任何**代寫健康資料或改寫對話的路徑存在。影子模式
        要呈現的是「現在真的能做什麼」，把不存在的能力寫成 True，前端就會
        渲染出一個後端根本沒有的入口。
        """
        if classification == "GENERAL":
            return True
        return action == "READ"

    async def can_notify(
        self,
        candidate_id: str,
        subject_owner_id: str,
        kind: NotificationKind,
        now: Optional[datetime] = None,
    ) -> bool:
        """某人是否為某種推播的合格收件人。

        走的是 `NOTIFICATION_POLICY`，**不是** `PERMISSIONS`。兩者回答的是
        不同問題：「他可以隨時去看我的健康資料」與「我出事時要通知他」是
        兩種不同的信任，一個人可能只給其中一種。

        當事人本人恆為收件人，不經這張表。收到通知 SHALL NOT 改變收件人的
        任何資料存取權——這裡只回答「送不送」，不回傳、也不衍生任何權限。
        """
        if candidate_id == subject_owner_id:
            return True
        role = await self.resolve_role(candidate_id, subject_owner_id, now=now)
        if role is None:
            return False
        return role in notification_recipient_roles(kind)

    async def notification_recipients(
        self,
        subject_owner_id: str,
        kind: NotificationKind,
        now: Optional[datetime] = None,
    ) -> List[str]:
        """某位當事人的某種推播，實際該送給誰。

        SHALL NOT 回傳「族譜全部成員」——收件人是判定的結果，不是規則。某個
        家庭恰好全員通過時它可以等於全員，但那是巧合。

        **與 `authorize`／`mask_response` 同樣尊重遷移狀態。** 收斂收件人也是
        一種收緊：導入前族譜全員都收得到，影子模式承諾「行為與導入前完全
        相同」，因此只在 `enforced` 才篩選。少了這一步，某位家人會在沒有任何
        切換的情況下突然收不到長輩的用藥風險通報——而通報是使用者最不該
        「安靜地少收到」的一種訊息。
        """
        tree = await self._get_tree(subject_owner_id)
        if tree is None:
            return []

        member_ids = [m.user_id for m in tree.family_members if m.user_id]
        state = await self.migration_state(subject_owner_id)
        if state != "enforced":
            return member_ids

        moment = now or datetime.now(tz=timezone.utc)
        recipients: List[str] = []
        for member_id in member_ids:
            if await self.can_notify(member_id, subject_owner_id, kind, now=moment):
                recipients.append(member_id)
        return recipients

    # ── 引導式角色指派 ────────────────────────────────────────────────

    async def role_assignment_status(
        self, owner_id: str
    ) -> FamilyRoleAssignmentStatus:
        """擁有者是否已完成引導式角色指派。

        完成條件：族譜中**每一位**現有成員都持有明確可解析的 `family_role`。
        欄位缺席即為未設定，即使其授權行為與 MEMBER 相同——授權上的預設值與
        指派上的完成判定是兩件事：前者確保沒有人因為缺欄位而取得超額權限，
        後者確保擁有者真的看過每一位成員並做出決定。

        判定落在**後端資料**，不採信前端旗標：前端旗標可以被清掉、可以在另一
        支裝置上不同步、也可以在使用者按了「完成」卻其實沒設定任何人時被設
        起來。要決定一個家庭能不能安全地進入強制，唯一可信的依據是那份族譜
        文件裡實際存了什麼。
        """
        tree = await self._get_tree(owner_id)
        if tree is None:
            return FamilyRoleAssignmentStatus(
                owner_id=owner_id,
                is_complete=False,
                unassigned_member_ids=[],
                rbac_migration_state=DEFAULT_MIGRATION_STATE,
            )
        unassigned = [
            m.user_id for m in tree.family_members if m.family_role is None
        ]
        return FamilyRoleAssignmentStatus(
            owner_id=owner_id,
            # 沒有任何成員時視為完成：沒有人要指派，不該把擁有者卡在引導畫面。
            is_complete=not unassigned,
            unassigned_member_ids=unassigned,
            rbac_migration_state=tree.rbac_migration_state,
        )
