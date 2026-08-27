import pytest

from scripts.normalize_family_medicine_department import (
    NEW_VALUE,
    OLD_VALUE,
    normalize_departments,
)


def test_replaces_exact_element():
    assert normalize_departments(["不分科", "家庭醫學科"]) == ["不分科", "家醫科"]


def test_replaces_inside_dirty_composite_element():
    """
    約 12 筆院所（多為醫學中心）把整串科別塞進單一元素。只比對整個元素會漏掉，
    必須做元素內的字串取代。
    """
    assert normalize_departments(["家庭醫學科、內科、外科、兒科"]) == [
        "家醫科、內科、外科、兒科"
    ]


def test_dedupes_when_both_spellings_present():
    """同時含兩種寫法的文件改寫後會出現重複值。"""
    assert normalize_departments(["家醫科", "內科", "家庭醫學科"]) == [
        "家醫科",
        "內科",
    ]


def test_dedupe_preserves_original_order():
    """順序在 Flex 卡片上看得到，不能用 set() 去重。"""
    assert normalize_departments(["牙科", "家庭醫學科", "內科", "家醫科"]) == [
        "牙科",
        "家醫科",
        "內科",
    ]


@pytest.mark.parametrize(
    "departments",
    [
        [],
        ["內科", "外科"],
        ["家醫科", "不分科"],
        ["牙科"],
    ],
)
def test_returns_none_when_nothing_to_change(departments):
    """回傳 None 代表不需更新，呼叫端才不會送出無意義的寫入。"""
    assert normalize_departments(departments) is None


def test_is_idempotent():
    """腳本可重複執行：第二次跑不應再有任何變更。"""
    first = normalize_departments(["家庭醫學科", "內科"])
    assert first == ["家醫科", "內科"]
    assert normalize_departments(first) is None


def test_keeps_non_string_elements_untouched():
    """
    不認得的元素原樣保留。資料清理不是這支腳本的職責，把它丟掉比留著更糟。
    """
    assert normalize_departments([None, "家庭醫學科", 123]) == [None, "家醫科", 123]


def test_migration_target_matches_alias_table():
    """
    腳本改寫的目標值必須是 department_matcher 的正規值，否則遷移完成後
    查詢會對不上。
    """
    from app.services.medical.department_matcher import (
        CANONICAL_DEPARTMENTS,
        DEPARTMENT_ALIASES,
    )

    assert NEW_VALUE in CANONICAL_DEPARTMENTS
    assert OLD_VALUE not in CANONICAL_DEPARTMENTS
    assert DEPARTMENT_ALIASES[OLD_VALUE] == NEW_VALUE
