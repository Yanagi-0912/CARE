"""授權式 PatientContext 建構器。"""

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.models.family_tree import FamilyMember, FamilyTree
from app.services.family.patient_context import ValueSource
from app.services.family.patient_context_service import PatientContextService


OPERATOR = "U_OPERATOR"
MEMBER_ID = "U_MEMBER"


def _tree(*members: FamilyMember) -> FamilyTree:
    now = datetime.now(tz=timezone.utc)
    return FamilyTree(
        user_id=OPERATOR,
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


class FakeAuthorization:
    def __init__(self, *, denied=False, error: Exception | None = None):
        self.denied = denied
        self.error = error
        self.calls = []

    async def authorize(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.error:
            raise self.error
        if self.denied:
            raise HTTPException(status_code=403, detail="forbidden")
        return "GUARDIAN"


class FakeProfiles:
    def __init__(self, profiles=None, error: Exception | None = None):
        self.profiles = profiles or {}
        self.error = error
        self.calls = []

    async def get_user_profile(self, user_id):
        self.calls.append(user_id)
        if self.error:
            raise self.error
        return self.profiles.get(user_id)


def _service(*, member=None, denied=False, auth_error=None, profile_error=None):
    trees = FakeTrees(_tree(member) if member else None)
    authorization = FakeAuthorization(denied=denied, error=auth_error)
    profiles = FakeProfiles(
        {
            OPERATOR: {"age": 40, "gender": "male"},
            MEMBER_ID: {"age": 35, "gender": "female"},
        },
        error=profile_error,
    )
    service = PatientContextService(
        family_tree_repository=trees,
        authorization_service=authorization,
        user_profile_service=profiles,
    )
    return service, trees, authorization, profiles


@pytest.mark.asyncio
async def test_self_profile_does_not_require_family_lookup_or_authorization():
    service, trees, authorization, profiles = _service()

    context = await service.resolve(OPERATOR, person="我")

    assert context.patient_id == OPERATOR
    assert (context.age, context.age_source) == (40, ValueSource.PROFILE)
    assert (context.gender, context.gender_source) == ("male", ValueSource.PROFILE)
    assert trees.calls == []
    assert authorization.calls == []
    assert profiles.calls == [OPERATOR]


@pytest.mark.asyncio
async def test_authorized_member_profile_uses_strict_sensitive_read():
    member = FamilyMember(
        user_id=MEMBER_ID,
        display_name="王美玲",
        relationship_type="spouse",
    )
    service, _, authorization, profiles = _service(member=member)

    context = await service.resolve(OPERATOR, person="王美玲")

    assert context.patient_id == MEMBER_ID
    assert (context.age, context.age_source) == (35, ValueSource.PROFILE)
    assert (context.gender, context.gender_source) == ("female", ValueSource.PROFILE)
    assert authorization.calls == [
        (
            (OPERATOR, MEMBER_ID, "SENSITIVE", "READ"),
            {"has_legacy_equivalent": False},
        )
    ]
    assert profiles.calls == [MEMBER_ID]


@pytest.mark.asyncio
async def test_message_values_override_an_authorized_profile():
    member = FamilyMember(
        user_id=MEMBER_ID,
        display_name="王美玲",
        relationship_type="spouse",
    )
    service, *_ = _service(member=member)

    context = await service.resolve(
        OPERATOR,
        person="王美玲",
        message_age=30,
        message_gender="male",
    )

    assert (context.age, context.age_source) == (30, ValueSource.MESSAGE)
    assert (context.gender, context.gender_source) == ("male", ValueSource.MESSAGE)


@pytest.mark.asyncio
async def test_denied_member_keeps_only_values_stated_in_the_message():
    member = FamilyMember(
        user_id=MEMBER_ID,
        display_name="王美玲",
        relationship_type="spouse",
    )
    service, _, _, profiles = _service(member=member, denied=True)

    context = await service.resolve(
        OPERATOR,
        person="王美玲",
        message_age=34,
    )

    assert (context.age, context.age_source) == (34, ValueSource.MESSAGE)
    assert context.gender == "unknown"
    assert context.gender_source is ValueSource.UNKNOWN
    assert profiles.calls == []


@pytest.mark.asyncio
async def test_care_recipient_flag_does_not_bypass_authorization():
    member = FamilyMember(
        user_id=MEMBER_ID,
        display_name="王美玲",
        relationship_type="spouse",
        is_care_recipient=True,
    )
    service, _, authorization, profiles = _service(member=member, denied=True)

    context = await service.resolve(OPERATOR, person="王美玲")

    assert context.patient_id == MEMBER_ID
    assert context.age is None
    assert len(authorization.calls) == 1
    assert profiles.calls == []


@pytest.mark.asyncio
async def test_external_id_cannot_select_an_unmentioned_family_member():
    member = FamilyMember(
        user_id=MEMBER_ID,
        display_name="王美玲",
        relationship_type="spouse",
    )
    service, _, authorization, profiles = _service(member=member)

    context = await service.resolve(
        OPERATOR,
        person="隔壁阿伯",
        external_patient_id=MEMBER_ID,
        message_age=70,
    )

    assert context.patient_kind == "not_found"
    assert context.patient_id is None
    assert (context.age, context.age_source) == (70, ValueSource.MESSAGE)
    assert authorization.calls == []
    assert profiles.calls == []


@pytest.mark.asyncio
async def test_external_id_cannot_redirect_a_resolved_member():
    member = FamilyMember(
        user_id=MEMBER_ID,
        display_name="王美玲",
        relationship_type="spouse",
    )
    service, _, authorization, profiles = _service(member=member)

    context = await service.resolve(
        OPERATOR,
        person="王美玲",
        external_patient_id="U_SOMEONE_ELSE",
    )

    assert context.patient_id == MEMBER_ID
    assert authorization.calls[0][0][1] == MEMBER_ID
    assert profiles.calls == [MEMBER_ID]


@pytest.mark.asyncio
async def test_ambiguous_person_does_not_authorize_or_read_profiles():
    first = FamilyMember(
        user_id="U_FIRST",
        display_name="大明",
        relationship_type="parent",
    )
    second = FamilyMember(
        user_id="U_SECOND",
        display_name="小明",
        relationship_type="parent",
    )
    trees = FakeTrees(_tree(first, second))
    authorization = FakeAuthorization()
    profiles = FakeProfiles()
    service = PatientContextService(
        family_tree_repository=trees,
        authorization_service=authorization,
        user_profile_service=profiles,
    )

    context = await service.resolve(OPERATOR, relationship="parent")

    assert context.patient_kind == "ambiguous"
    assert {candidate.user_id for candidate in context.candidates} == {
        "U_FIRST",
        "U_SECOND",
    }
    assert authorization.calls == []
    assert profiles.calls == []


@pytest.mark.asyncio
async def test_authorization_error_fails_closed_without_losing_message_values():
    member = FamilyMember(
        user_id=MEMBER_ID,
        display_name="王美玲",
        relationship_type="spouse",
    )
    service, _, _, profiles = _service(
        member=member,
        auth_error=RuntimeError("authorization unavailable"),
    )

    context = await service.resolve(OPERATOR, person="王美玲", message_gender="female")

    assert context.gender == "female"
    assert context.gender_source is ValueSource.MESSAGE
    assert context.age is None
    assert profiles.calls == []


@pytest.mark.asyncio
async def test_profile_error_fails_closed_after_successful_authorization():
    member = FamilyMember(
        user_id=MEMBER_ID,
        display_name="王美玲",
        relationship_type="spouse",
    )
    service, _, authorization, profiles = _service(
        member=member,
        profile_error=RuntimeError("profile unavailable"),
    )

    context = await service.resolve(OPERATOR, person="王美玲", message_age=35)

    assert (context.age, context.age_source) == (35, ValueSource.MESSAGE)
    assert len(authorization.calls) == 1
    assert profiles.calls == [MEMBER_ID]


@pytest.mark.asyncio
async def test_family_tree_error_returns_unlinked_context_without_profile_access():
    trees = FakeTrees(error=RuntimeError("tree unavailable"))
    authorization = FakeAuthorization()
    profiles = FakeProfiles({MEMBER_ID: {"age": 35, "gender": "female"}})
    service = PatientContextService(
        family_tree_repository=trees,
        authorization_service=authorization,
        user_profile_service=profiles,
    )

    context = await service.resolve(OPERATOR, person="王美玲", message_age=35)

    assert context.patient_kind == "not_found"
    assert (context.age, context.age_source) == (35, ValueSource.MESSAGE)
    assert authorization.calls == []
    assert profiles.calls == []


# --- 只解析人物、不讀健康資料（緊急流程用，10.14）-----------------------------


@pytest.mark.asyncio
async def test_resolve_person_reads_only_the_family_list():
    """緊急流程只拿它決定稱謂：命中家人不得順便授權或讀 profile。"""
    member = FamilyMember(
        user_id=MEMBER_ID, display_name="王大明", relationship_type="grandparent"
    )
    service, trees, authorization, profiles = _service(member=member)

    resolution = await service.resolve_person(
        OPERATOR, person="阿公", relationship="grandparent"
    )

    assert resolution.kind == "member"
    assert resolution.member.user_id == MEMBER_ID
    assert trees.calls == [OPERATOR]
    assert authorization.calls == []
    assert profiles.calls == []


@pytest.mark.asyncio
async def test_resolve_person_with_two_matches_is_ambiguous():
    grandpas = [
        FamilyMember(user_id=f"U_G{i}", display_name=name, relationship_type="grandparent")
        for i, name in enumerate(("王大明", "李阿土"))
    ]
    service = PatientContextService(
        family_tree_repository=FakeTrees(_tree(*grandpas)),
        authorization_service=FakeAuthorization(),
        user_profile_service=FakeProfiles(),
    )

    resolution = await service.resolve_person(
        OPERATOR, person="阿公", relationship="grandparent"
    )

    assert resolution.kind == "ambiguous"
    assert resolution.member is None


@pytest.mark.asyncio
async def test_resolve_person_when_family_list_fails_never_picks_a_member():
    service = PatientContextService(
        family_tree_repository=FakeTrees(error=RuntimeError("mongo down")),
        authorization_service=FakeAuthorization(),
        user_profile_service=FakeProfiles(),
    )

    resolution = await service.resolve_person(
        OPERATOR, person="阿公", relationship="grandparent"
    )

    assert resolution.kind == "not_found"
    assert resolution.member is None
