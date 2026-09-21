"""PatientContext 的資料歸屬與優先序。"""

from dataclasses import FrozenInstanceError

import pytest

from app.models.family_tree import FamilyMember
from app.services.family.patient_context import ValueSource, build_patient_context
from app.services.family.person_resolution import PersonResolution


MEMBER = FamilyMember(
    user_id="U_MEMBER",
    display_name="王美玲",
    relationship_type="spouse",
)


def _resolution(kind="self", **kwargs):
    return PersonResolution(kind=kind, **kwargs)


def test_self_context_uses_the_operator_as_patient():
    context = build_patient_context(
        operator_id="U_OPERATOR",
        resolution=_resolution("self", matched_by="default"),
    )
    assert context.patient_kind == "self"
    assert context.patient_id == "U_OPERATOR"
    assert context.age is None
    assert context.age_source is ValueSource.UNKNOWN
    assert context.gender == "unknown"
    assert context.gender_source is ValueSource.UNKNOWN


def test_member_context_keeps_identity_and_display_fields():
    context = build_patient_context(
        operator_id="U_OPERATOR",
        resolution=_resolution(
            "member",
            member=MEMBER,
            display_label="王美玲",
            relationship="spouse",
            matched_by="name_and_relationship",
        ),
    )
    assert context.patient_id == "U_MEMBER"
    assert context.display_label == "王美玲"
    assert context.relationship == "spouse"


def test_message_values_override_profile_values():
    context = build_patient_context(
        operator_id="U_OPERATOR",
        resolution=_resolution("member", member=MEMBER),
        message_age=5,
        message_gender="female",
        profile_age=40,
        profile_gender="male",
    )
    assert (context.age, context.age_source) == (5, ValueSource.MESSAGE)
    assert (context.gender, context.gender_source) == (
        "female",
        ValueSource.MESSAGE,
    )


def test_profile_values_are_used_when_message_values_are_absent():
    context = build_patient_context(
        operator_id="U_OPERATOR",
        resolution=_resolution("member", member=MEMBER),
        profile_age=40,
        profile_gender="female",
    )
    assert (context.age, context.age_source) == (40, ValueSource.PROFILE)
    assert (context.gender, context.gender_source) == (
        "female",
        ValueSource.PROFILE,
    )


def test_unlinked_person_ignores_profile_but_keeps_message_values():
    context = build_patient_context(
        operator_id="U_OPERATOR",
        resolution=_resolution("not_found", display_label="隔壁阿伯"),
        message_age=70,
        profile_age=40,
        profile_gender="male",
    )
    assert context.patient_id is None
    assert (context.age, context.age_source) == (70, ValueSource.MESSAGE)
    assert (context.gender, context.gender_source) == (
        "unknown",
        ValueSource.UNKNOWN,
    )


@pytest.mark.parametrize("kind", ["ambiguous", "conflict"])
def test_unresolved_person_never_uses_profile_values(kind):
    context = build_patient_context(
        operator_id="U_OPERATOR",
        resolution=_resolution(kind, candidates=(MEMBER,)),
        profile_age=40,
        profile_gender="female",
    )
    assert context.patient_id is None
    assert context.age is None
    assert context.age_source is ValueSource.UNKNOWN
    assert context.gender == "unknown"
    assert context.gender_source is ValueSource.UNKNOWN
    assert context.candidates[0].user_id == "U_MEMBER"


@pytest.mark.parametrize("age", [True, "5", -1, 131])
def test_invalid_message_age_falls_back_to_profile(age):
    context = build_patient_context(
        operator_id="U_OPERATOR",
        resolution=_resolution("self"),
        message_age=age,
        profile_age=40,
    )
    assert (context.age, context.age_source) == (40, ValueSource.PROFILE)


def test_unknown_gender_does_not_override_a_known_profile_gender():
    context = build_patient_context(
        operator_id="U_OPERATOR",
        resolution=_resolution("self"),
        message_gender="unknown",
        profile_gender="male",
    )
    assert (context.gender, context.gender_source) == (
        "male",
        ValueSource.PROFILE,
    )


def test_context_is_immutable():
    context = build_patient_context(
        operator_id="U_OPERATOR",
        resolution=_resolution("self"),
    )
    with pytest.raises(FrozenInstanceError):
        context.age = 12


def test_operator_id_is_required():
    with pytest.raises(ValueError, match="operator_id"):
        build_patient_context(operator_id="", resolution=_resolution("self"))


def test_member_resolution_must_carry_a_member():
    with pytest.raises(ValueError, match="缺少 member"):
        build_patient_context(
            operator_id="U_OPERATOR",
            resolution=_resolution("member"),
        )
