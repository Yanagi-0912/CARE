"""FamilyTreeRepository 的角色指派與遷移狀態。

兩件事在這裡守住：

1. `OWNER` 進不了 `family_role`——這條檢查不能只靠 Pydantic，因為 `$set` 是
   直接寫進陣列元素的，模型驗證不會在寫入路徑上執行。
2. 角色指派與加入成員都 SHALL NOT 觸碰 `rbac_migration_state`——否則
   「新增家庭成員 → 觸發 legacy fallback」這條提權路徑就成立了。
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.family_tree import FamilyMember
from app.repositories.family_tree_repository import FamilyTreeRepository

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
OWNER = "U-owner"
MEMBER = "U-member"


def make_collection(matched: int = 1, doc=None):
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=MagicMock(matched_count=matched))
    collection.find_one = AsyncMock(
        return_value=doc
        or {
            "user_id": OWNER,
            "family_members": [{"user_id": MEMBER, "family_role": "GUARDIAN"}],
            "rbac_migration_state": "enforced",
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    return collection


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["GUARDIAN", "CAREGIVER", "MEMBER"])
async def test_set_family_role_writes_assignable_roles(role):
    collection = make_collection()
    tree = await FamilyTreeRepository.set_family_role(
        OWNER, MEMBER, role, collection=collection
    )
    assert tree is not None
    query, update = collection.update_one.await_args.args
    assert query == {"user_id": OWNER, "family_members.user_id": MEMBER}
    assert update["$set"]["family_members.$.family_role"] == role


@pytest.mark.asyncio
async def test_set_family_role_rejects_owner_before_touching_the_database():
    """OWNER 是推導值，寫入它等於讓渡資料所有權。"""
    collection = make_collection()
    with pytest.raises(ValueError):
        await FamilyTreeRepository.set_family_role(
            OWNER, MEMBER, "OWNER", collection=collection
        )
    collection.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_family_role_rejects_unknown_role():
    collection = make_collection()
    with pytest.raises(ValueError):
        await FamilyTreeRepository.set_family_role(
            OWNER, MEMBER, "ADMIN", collection=collection
        )
    collection.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_family_role_does_not_touch_migration_state():
    """角色指派 SHALL NOT 成為讓某個家庭退回 legacy 授權的路徑。"""
    collection = make_collection()
    await FamilyTreeRepository.set_family_role(
        OWNER, MEMBER, "CAREGIVER", collection=collection
    )
    _, update = collection.update_one.await_args.args
    assert "rbac_migration_state" not in str(update)


@pytest.mark.asyncio
async def test_set_family_role_only_touches_the_named_member():
    """定位子 `family_members.$` 只會命中查詢條件指到的那一個元素。"""
    collection = make_collection()
    await FamilyTreeRepository.set_family_role(
        OWNER, MEMBER, "GUARDIAN", collection=collection
    )
    _, update = collection.update_one.await_args.args
    assert list(update["$set"].keys()) == [
        "family_members.$.family_role",
        "updated_at",
    ]


@pytest.mark.asyncio
async def test_set_family_role_returns_none_when_member_absent():
    collection = make_collection(matched=0)
    result = await FamilyTreeRepository.set_family_role(
        OWNER, "U-not-there", "MEMBER", collection=collection
    )
    assert result is None


@pytest.mark.asyncio
async def test_add_member_does_not_touch_migration_state():
    """「新增家庭成員 → 觸發 legacy fallback」是一條提權路徑，必須封死。

    加入成員是低成本、可重複、看起來完全無害的動作。若它會讓整個家庭退回
    變更前的寬鬆行為，任何能建立邀請的人只要拉一個新帳號進來，本能力的所有
    約束就一次全部失效。
    """
    collection = MagicMock()
    collection.update_one = AsyncMock(return_value=MagicMock(matched_count=1))
    collection.find_one = AsyncMock(
        return_value={
            "user_id": OWNER,
            "family_members": [],
            "rbac_migration_state": "enforced",
            "created_at": NOW,
            "updated_at": NOW,
        }
    )
    # add_member 目前不接受注入的 collection，改以真實呼叫路徑之外的方式檢查：
    # 直接讀原始碼確認它的 $set 只動 updated_at。這比放過這條規則好——
    # 它是本 change 唯一一條「看起來無害卻能關掉整套授權」的路徑。
    import inspect

    source = inspect.getsource(FamilyTreeRepository.add_member)
    assert "rbac_migration_state" not in source
    assert '"$set": {"updated_at": now}' in source


@pytest.mark.asyncio
async def test_new_member_arrives_without_a_role():
    """新成員未設定角色即以 MEMBER 處理，且「未設定」這個狀態要保得住。"""
    member = FamilyMember(user_id="U-newcomer")
    assert member.family_role is None
    assert member.effective_family_role == "MEMBER"


@pytest.mark.asyncio
async def test_set_migration_state_is_the_only_writer_of_that_field():
    collection = make_collection()
    await FamilyTreeRepository.set_migration_state(
        OWNER, "enforced", collection=collection
    )
    _, update = collection.update_one.await_args.args
    assert update["$set"]["rbac_migration_state"] == "enforced"


@pytest.mark.asyncio
async def test_set_migration_state_rejects_unknown_state():
    collection = make_collection()
    with pytest.raises(ValueError):
        await FamilyTreeRepository.set_migration_state(
            OWNER, "legacy", collection=collection
        )
    collection.update_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_other_setters_do_not_touch_migration_state():
    """set_relationship／set_care_recipient 也不得成為回退的路徑。"""
    import inspect

    for method in (
        FamilyTreeRepository.set_relationship,
        FamilyTreeRepository.set_care_recipient,
    ):
        assert "rbac_migration_state" not in inspect.getsource(method)


# ── 既有文件的相容性（tasks 4.6）────────────────────────────────────


@pytest.mark.asyncio
async def test_existing_document_without_family_role_reads_back_as_member():
    """既有族譜文件沒有 `family_role` 欄位，讀回時不得炸、也不得憑空補值。

    這是「不做 backfill」的前提：授權上視為 MEMBER（不會有人因缺欄位而取得
    超額權限），但欄位本身仍是 None——指派上的「未設定」要保得住。
    """
    from app.models.family_tree import FamilyTree

    legacy_doc = {
        "user_id": OWNER,
        "family_members": [
            {"user_id": MEMBER, "relationship_type": "child"},  # 沒有 family_role
        ],
        "created_at": NOW,
        "updated_at": NOW,
        # 也沒有 rbac_migration_state
    }
    tree = FamilyTree(**legacy_doc)
    member = tree.family_members[0]

    assert member.family_role is None
    assert member.effective_family_role == "MEMBER"
    assert tree.rbac_migration_state == "shadow"


# ── 兩個方向的角色與單次查詢（tasks 9.1／9.3）──────────────────────


def make_batch_collection(docs):
    collection = MagicMock()
    cursor = MagicMock()
    cursor.to_list = AsyncMock(return_value=docs)
    collection.find.return_value = cursor
    return collection


@pytest.mark.asyncio
async def test_roles_for_operator_uses_a_single_query_for_many_members():
    """族譜頁一次可能有十餘位成員，逐一查詢的延遲在長輩的行動網路上看得見。"""
    owner_ids = [f"U-{i}" for i in range(10)]
    collection = make_batch_collection(
        [
            {
                "user_id": owner_id,
                "family_members": [{"user_id": MEMBER, "family_role": "GUARDIAN"}],
                "rbac_migration_state": "enforced",
            }
            for owner_id in owner_ids
        ]
    )

    result = await FamilyTreeRepository.get_roles_for_operator(
        MEMBER, owner_ids, collection=collection
    )

    assert collection.find.call_count == 1
    assert len(result) == 10


@pytest.mark.asyncio
async def test_roles_for_operator_reads_the_direction_that_lives_in_the_other_tree():
    """回的是「**我對他的**資料是什麼角色」——存在對方的文件裡。

    方向很容易讀反：我的族譜記的是「他對我的資料」的角色。這條測試釘住的是
    這支方法查的是對方那一份。
    """
    collection = make_batch_collection(
        [
            {
                "user_id": "U-elder",
                "family_members": [
                    {"user_id": MEMBER, "family_role": "CAREGIVER"},
                    {"user_id": "U-someone-else", "family_role": "GUARDIAN"},
                ],
                "rbac_migration_state": "enforced",
            }
        ]
    )

    result = await FamilyTreeRepository.get_roles_for_operator(
        MEMBER, ["U-elder"], collection=collection
    )

    # 拿到的是 MEMBER 自己那一筆，不是族譜裡第一個人的角色
    assert result["U-elder"]["family_role"] == "CAREGIVER"
    assert result["U-elder"]["rbac_migration_state"] == "enforced"


@pytest.mark.asyncio
async def test_operator_absent_from_a_tree_is_omitted_not_defaulted():
    """不在對方族譜裡就沒有任何角色——SHALL NOT 給預設值。

    family boundary 是最外層的閘門，缺席不該被解讀成 MEMBER。
    """
    collection = make_batch_collection(
        [
            {
                "user_id": "U-stranger-tree",
                "family_members": [{"user_id": "U-someone-else"}],
                "rbac_migration_state": "enforced",
            }
        ]
    )

    result = await FamilyTreeRepository.get_roles_for_operator(
        MEMBER, ["U-stranger-tree"], collection=collection
    )

    assert result == {}


@pytest.mark.asyncio
async def test_roles_for_operator_short_circuits_on_empty_input():
    """沒有成員時不該白跑一趟資料庫。"""
    collection = make_batch_collection([])
    assert await FamilyTreeRepository.get_roles_for_operator(
        MEMBER, [], collection=collection
    ) == {}
    collection.find.assert_not_called()
