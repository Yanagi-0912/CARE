"""查服藥狀況的身份對照：使用者說「媽媽」「美玲」時，對到家人名單裡的哪一位。

名單只在發問者自己的族譜裡找（加入家族是雙向的，家人一定在他自己的名單裡），
範圍通常只有兩三個人。對不準時一律反問，不猜——猜錯就是把另一位家人的用藥
講給他聽。
"""

from app.models.family_tree import FamilyMember
from app.services.medication.medication_status_service import resolve_person


def _member(user_id, name=None, relationship=None):
    return FamilyMember(user_id=user_id, display_name=name, relationship_type=relationship)


MOM = _member("U_MOM", "王美玲", "parent")
DAD = _member("U_DAD", "王大明", "parent")
SON = _member("U_SON", "小明", "child")


def test_no_person_means_the_asker_themselves():
    assert resolve_person([MOM], person="", relationship="").kind == "self"


def test_first_person_words_mean_the_asker_themselves():
    """prompt 要模型問自己時留空，但模型偶爾會把「我」原樣填進來。"""
    for word in ("我", "自己", " 我自己 ", "me"):
        assert resolve_person([MOM], person=word, relationship="").kind == "self"


def test_part_of_a_name_matches_that_member():
    result = resolve_person([MOM, DAD], person="美玲", relationship="")
    assert result.kind == "member"
    assert result.member.user_id == "U_MOM"


def test_kinship_term_matches_by_relationship():
    result = resolve_person([MOM, SON], person="媽媽", relationship="parent")
    assert result.kind == "member"
    assert result.member.user_id == "U_MOM"


def test_relationship_alone_is_enough():
    result = resolve_person([MOM, SON], person="", relationship="child")
    assert result.kind == "member"
    assert result.member.user_id == "U_SON"


def test_two_members_with_the_same_relationship_is_ambiguous():
    """關係只到「父／母」，爸媽都在名單裡時分不出來，要反問而不是猜。"""
    result = resolve_person([MOM, DAD, SON], person="媽媽", relationship="parent")
    assert result.kind == "ambiguous"
    assert {m.user_id for m in result.candidates} == {"U_MOM", "U_DAD"}


def test_a_name_shared_by_two_members_is_ambiguous():
    result = resolve_person([MOM, DAD], person="王", relationship="")
    assert result.kind == "ambiguous"
    assert {m.user_id for m in result.candidates} == {"U_MOM", "U_DAD"}


def test_name_is_tried_before_relationship():
    result = resolve_person([MOM, DAD], person="大明", relationship="parent")
    assert result.kind == "member"
    assert result.member.user_id == "U_DAD"


def test_unset_relationship_cannot_be_matched_by_kinship_term():
    """關係是加入後才在 LIFF 設定的，沒設定的人只能靠名字對到。"""
    unset = _member("U_X", "阿珠", None)
    assert resolve_person([unset], person="媽媽", relationship="parent").kind == "not_found"


def test_english_kinship_word_from_the_model_is_normalised():
    """relationship 是模型填的，偶爾會寫成 mother 而不是 parent。"""
    result = resolve_person([MOM, SON], person="媽媽", relationship="mother")
    assert result.kind == "member"
    assert result.member.user_id == "U_MOM"


def test_unknown_person_is_not_found():
    result = resolve_person([MOM, DAD], person="阿嬤", relationship="grandparent")
    assert result.kind == "not_found"


def test_member_without_display_name_is_never_matched_by_name():
    nameless = _member("U_N", None, None)
    assert resolve_person([nameless], person="美玲", relationship="").kind == "not_found"
