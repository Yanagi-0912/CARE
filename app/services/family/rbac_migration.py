"""家庭 RBAC 的遷移：擁有者什麼時候從影子模式切到強制。

強制以擁有者為邊界逐一啟用（見 `FamilyTree.rbac_migration_state`）。一個家庭能不能
安全地進入強制，唯一的依據是族譜裡是否**每一位**成員都有擁有者親自指派的角色
（與 `FamilyAuthorizationService.role_assignment_status` 同一個判定）：還有人沒設定
就切，那個人會以 MEMBER 處理，在沒有人做過決定的情況下突然看不到長輩的健康資料。

所以切換的時機就是「擁有者替最後一位成員做完決定」的那一刻——指派了最後一位，
或移除了最後一位未設定者。這個狀態**只往前走**：這裡永遠不會把人切回 shadow。
要全體退回變更前的行為，用的是全域總閘 `FAMILY_RBAC_ENFORCED`，不是逐一改資料。

寫入一律經 `FamilyTreeRepository.set_migration_state`（唯一會動這個欄位的方法），
這裡是它唯一的呼叫端。
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def enforce_if_assignment_complete(trees: Any, owner_id: str) -> bool:
    """擁有者的每位成員都已指派角色時，把他切成 enforced。回傳這次有沒有切換。

    沒有成員的擁有者不切：沒有任何人需要決定，也就沒有「做完決定」這件事。
    """
    tree = await trees.get_by_user_id(owner_id)
    if tree is None or not tree.family_members:
        return False
    if tree.rbac_migration_state == "enforced":
        return False
    if any(member.family_role is None for member in tree.family_members):
        return False

    await trees.set_migration_state(owner_id, "enforced")
    logger.info(
        "家庭權限改為強制：owner=%s, members=%d",
        owner_id,
        len(tree.family_members),
    )
    return True
