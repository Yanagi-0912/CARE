"""登入者自己的家庭名單與稱謂查詢。"""

from datetime import datetime, timezone

import pytest

from app.core.user_language import SUPPORTED_LANGUAGES
from app.models.family_tree import FamilyMember, FamilyTree
from app.services.family.family_directory_service import FamilyDirectoryService


def _tree(*members: FamilyMember) -> FamilyTree:
    now = datetime.now(tz=timezone.utc)
    return FamilyTree(
        user_id="U_ME",
        family_members=list(members),
        created_at=now,
        updated_at=now,
    )


class FakeTrees:
    def __init__(self, tree=None, error: Exception | None = None):
        self.tree = tree
        self.error = error
        self.calls = []

    async def get_by_user_id(self, user_id):
        self.calls.append(user_id)
        if self.error:
            raise self.error
        return self.tree


@pytest.mark.asyncio
async def test_lists_all_members_with_relationships_and_unset_labels():
    trees = FakeTrees(
        _tree(
            FamilyMember(
                user_id="U_MOM", display_name="王美玲", relationship_type="parent"
            ),
            FamilyMember(user_id="U_OTHER", display_name="陳小華"),
        )
    )

    text = await FamilyDirectoryService(trees).describe("U_ME", language="zh-TW")

    assert text == "您的家庭名單：\n王美玲：父／母\n陳小華：尚未設定稱謂"
    assert "U_MOM" not in text
    assert trees.calls == ["U_ME"]


@pytest.mark.asyncio
async def test_relationship_query_lists_every_matching_parent():
    trees = FakeTrees(
        _tree(
            FamilyMember(
                user_id="U_MOM", display_name="王美玲", relationship_type="parent"
            ),
            FamilyMember(
                user_id="U_DAD", display_name="陳大明", relationship_type="parent"
            ),
            FamilyMember(
                user_id="U_SPOUSE", display_name="林小芬", relationship_type="spouse"
            ),
        )
    )

    text = await FamilyDirectoryService(trees).describe(
        "U_ME", relationship="parent", language="zh-TW"
    )

    assert text == "您設定為父／母的家人：王美玲、陳大明。"


@pytest.mark.asyncio
async def test_name_query_returns_the_saved_relationship():
    trees = FakeTrees(
        _tree(
            FamilyMember(
                user_id="U_SPOUSE", display_name="王美玲", relationship_type="spouse"
            )
        )
    )

    text = await FamilyDirectoryService(trees).describe(
        "U_ME", person="王美玲", language="zh-TW"
    )

    assert text == "您將王美玲設定為配偶。"


@pytest.mark.asyncio
async def test_name_query_reports_an_unset_relationship():
    trees = FakeTrees(
        _tree(FamilyMember(user_id="U_MEMBER", display_name="王美玲"))
    )

    text = await FamilyDirectoryService(trees).describe(
        "U_ME", person="王美玲", language="zh-TW"
    )

    assert text == "王美玲在您的家庭名單中，但尚未設定稱謂。"


@pytest.mark.asyncio
async def test_name_query_does_not_expose_a_user_id_when_name_is_missing():
    trees = FakeTrees(
        _tree(FamilyMember(user_id="U_SECRET", relationship_type="spouse"))
    )

    text = await FamilyDirectoryService(trees).describe("U_ME", language="zh-TW")

    assert "未設定名字的家人：配偶" in text
    assert "U_SECRET" not in text


@pytest.mark.asyncio
async def test_similar_names_require_the_full_name():
    trees = FakeTrees(
        _tree(
            FamilyMember(user_id="U_1", display_name="王小明"),
            FamilyMember(user_id="U_2", display_name="陳小明"),
        )
    )

    text = await FamilyDirectoryService(trees).describe(
        "U_ME", person="小明", language="zh-TW"
    )

    assert text == "找到多位名稱相近的家人：王小明、陳小明。請說完整姓名。"


@pytest.mark.asyncio
async def test_missing_name_and_relationship_return_visible_no_match_messages():
    trees = FakeTrees(
        _tree(
            FamilyMember(
                user_id="U_MEMBER", display_name="王美玲", relationship_type="spouse"
            )
        )
    )
    service = FamilyDirectoryService(trees)

    assert await service.describe(
        "U_ME", person="陳小華", language="zh-TW"
    ) == "您的家庭名單中找不到「陳小華」。"
    assert await service.describe(
        "U_ME", relationship="parent", language="zh-TW"
    ) == "您的家庭名單中，沒有設定為父／母的成員。"


@pytest.mark.asyncio
async def test_empty_tree_and_repository_failure_have_distinct_messages():
    empty = await FamilyDirectoryService(FakeTrees()).describe(
        "U_ME", language="zh-TW"
    )
    failed = await FamilyDirectoryService(
        FakeTrees(error=RuntimeError("database unavailable"))
    ).describe("U_ME", language="zh-TW")

    assert empty == "您的家庭名單目前是空的。"
    assert failed == "暫時查不到家庭名單，請稍後再試。"


@pytest.mark.asyncio
async def test_unsupported_relationship_is_not_treated_as_self():
    trees = FakeTrees(_tree(FamilyMember(user_id="U_MEMBER", display_name="王美玲")))

    text = await FamilyDirectoryService(trees).describe(
        "U_ME", relationship="friend", language="zh-TW"
    )

    assert text == "這個稱謂目前不在可查詢的家庭關係中。"


@pytest.mark.asyncio
@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
async def test_visible_results_exist_in_every_supported_language(language):
    trees = FakeTrees(
        _tree(
            FamilyMember(
                user_id="U_MEMBER", display_name="Alex", relationship_type="parent"
            )
        )
    )
    service = FamilyDirectoryService(trees)

    outputs = [
        await service.describe("U_ME", language=language),
        await service.describe("U_ME", relationship="parent", language=language),
        await service.describe("U_ME", person="Alex", language=language),
    ]

    assert all(output.strip() for output in outputs)
    assert all("family.directory" not in output for output in outputs)
