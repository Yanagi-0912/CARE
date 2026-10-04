"""共用人物解析契約；各消費模組不得自行猜測另一套人物規則。"""

from app.models.family_tree import FamilyMember
from app.services.family.person_resolution import resolve_person


def _member(user_id, name=None, relationship=None):
    return FamilyMember(
        user_id=user_id,
        display_name=name,
        relationship_type=relationship,
    )


MOM = _member("U_MOM", "王美玲", "parent")
SON = _member("U_SON", "小明", "child")


def test_default_self_records_how_it_was_resolved():
    result = resolve_person([MOM], person="", relationship="")
    assert result.kind == "self"
    assert result.matched_by == "default"
    assert result.display_label == ""
    assert result.relationship is None


def test_explicit_self_word_records_how_it_was_resolved():
    result = resolve_person([MOM], person="本人", relationship="")
    assert result.kind == "self"
    assert result.matched_by == "self_word"


def test_name_match_exposes_a_stable_display_label_and_relationship():
    result = resolve_person([MOM], person="美玲", relationship="")
    assert result.kind == "member"
    assert result.member == MOM
    assert result.display_label == "王美玲"
    assert result.relationship == "parent"
    assert result.matched_by == "name"


def test_name_and_relationship_match_records_both_signals():
    result = resolve_person([MOM], person="美玲", relationship="mother")
    assert result.kind == "member"
    assert result.relationship == "parent"
    assert result.matched_by == "name_and_relationship"


def test_relationship_match_uses_member_name_as_display_label():
    result = resolve_person([SON], person="兒子", relationship="son")
    assert result.kind == "member"
    assert result.display_label == "小明"
    assert result.relationship == "child"
    assert result.matched_by == "relationship"


def test_relationship_match_without_member_name_keeps_requested_label():
    spouse = _member("U_SPOUSE", None, "spouse")
    result = resolve_person([spouse], person="老婆", relationship="wife")
    assert result.kind == "member"
    assert result.display_label == "老婆"


def test_name_and_relationship_conflict_is_not_silently_resolved():
    result = resolve_person([MOM, SON], person="美玲", relationship="child")
    assert result.kind == "conflict"
    assert result.member is None
    assert result.candidates == (MOM,)
    assert result.display_label == "美玲"
    assert result.relationship == "child"


def test_multiple_name_matches_with_no_matching_relationship_are_a_conflict():
    first = _member("U_1", "王小明", "parent")
    second = _member("U_2", "李小明", "sibling")
    result = resolve_person(
        [first, second], person="小明", relationship="spouse"
    )
    assert result.kind == "conflict"
    assert result.candidates == (first, second)


def test_ambiguous_result_records_candidates_and_match_source():
    first = _member("U_1", "大寶", "child")
    second = _member("U_2", "二寶", "child")
    result = resolve_person(
        [first, second], person="小孩", relationship="child"
    )
    assert result.kind == "ambiguous"
    assert result.candidates == (first, second)
    assert result.relationship == "child"
    assert result.matched_by == "relationship"


def test_unlinked_person_preserves_a_label_without_matching_a_member():
    result = resolve_person([MOM], person="隔壁阿伯", relationship="")
    assert result.kind == "not_found"
    assert result.display_label == "隔壁阿伯"
    assert result.member is None
    assert result.candidates == ()


def test_unknown_relationship_is_not_mistaken_for_self():
    result = resolve_person([MOM], person="", relationship="cousin")
    assert result.kind == "not_found"
    assert result.display_label == "cousin"
    assert result.matched_by == "none"


def test_external_user_id_in_person_text_does_not_match_member_id():
    result = resolve_person([MOM], person="U_MOM", relationship="")
    assert result.kind == "not_found"
    assert result.member is None


# --- 稱謂分性別（father／mother…）與只設大類的舊資料並存 ---

DAD = _member("U_DAD", "王大明", "father")
MOTHER = _member("U_MOTHER", "李春嬌", "mother")
LEGACY_A = _member("U_A", "甲", "parent")
LEGACY_B = _member("U_B", "乙", "parent")


def test_gendered_relationship_picks_the_right_parent():
    """爸媽都在名單：說「我爸」只對到設成爸爸的人，不用再反問。"""
    result = resolve_person([DAD, MOTHER], person="爸", relationship="father")
    assert result.kind == "member"
    assert result.member == DAD
    assert result.relationship == "father"


def test_english_kinship_alias_maps_to_the_gendered_value():
    result = resolve_person([DAD, MOTHER], person="mom", relationship="mom")
    assert result.member == MOTHER


def test_group_relationship_still_covers_gendered_members():
    """說「我父母」不分性別：爸爸、媽媽都算，兩位就反問。"""
    result = resolve_person([DAD, MOTHER], person="父母", relationship="parent")
    assert result.kind == "ambiguous"
    assert [m.user_id for m in result.candidates] == ["U_DAD", "U_MOTHER"]


def test_gendered_request_falls_back_to_members_without_gender():
    """族譜只設了「父母」的舊資料：說「我爸」仍要對得到，不能因為細分了就查無。"""
    result = resolve_person([LEGACY_A], person="爸", relationship="father")
    assert result.kind == "member"
    assert result.member == LEGACY_A
    assert result.relationship == "parent"


def test_two_members_without_gender_stay_ambiguous():
    result = resolve_person([LEGACY_A, LEGACY_B], person="爸", relationship="father")
    assert result.kind == "ambiguous"


def test_exact_gender_wins_over_members_without_gender():
    result = resolve_person([DAD, LEGACY_B], person="爸", relationship="father")
    assert result.member == DAD


def test_member_set_as_the_other_gender_never_matches():
    """設成媽媽的人永遠不算爸爸，即使名單只有她一位。"""
    result = resolve_person([MOTHER], person="爸", relationship="father")
    assert result.kind == "not_found"


def test_name_with_conflicting_gender_is_reported_as_conflict():
    result = resolve_person([MOTHER], person="春嬌", relationship="father")
    assert result.kind == "conflict"
