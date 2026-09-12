"""釘住實際出貨的中西藥交互作用配對表。"""

import json
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[3] / "resources" / "tcm_drug_interactions.json"


@pytest.fixture(scope="module")
def payload() -> dict:
    return json.loads(PATH.read_text(encoding="utf-8"))


def test_has_both_kinds_of_western_entry(payload):
    kinds = {p["western_kind"] for p in payload["pairs"]}
    assert kinds == {"ingredient", "class"}


def test_named_ingredients_dominate(payload):
    """具名成分才用得上；類別列收進來是為了不讓後人以為資料源沒有這些。"""
    pairs = payload["pairs"]
    named = [p for p in pairs if p["western_kind"] == "ingredient"]
    assert len(named) > len(pairs) * 0.9


def test_named_ingredients_are_uppercase_latin(payload):
    """具名成分一律大寫，與 drug_catalog.json 的 ingredients 一致。"""
    for pair in payload["pairs"]:
        if pair["western_kind"] == "ingredient":
            assert pair["western"] == pair["western"].upper()
            assert not any("一" <= c <= "鿿" for c in pair["western"])


def test_known_data_defects_are_fixed(payload):
    """實測的三個瑕疵：ﬂ 合字、Cyclosporin/Cyclosporine 兩種拼法、Nifedipne
    拼錯。逐一列舉修正而不做模糊比對——猜錯會把兩種不同的藥合併。"""
    names = {p["western"] for p in payload["pairs"]}

    assert "CIPROFLOXACIN" in names
    # 合字本身不得留在任何名稱裡。**不能寫成 "CIPROﬂOXACIN".upper() not in
    # names**——Python 的 upper() 會把 ﬂ 展開成 FL，那樣等於在斷言正確的名稱
    # 不存在，永遠會失敗。
    assert not any("ﬂ" in n or "ﬁ" in n for n in names)
    assert "CYCLOSPORIN" not in names
    assert "NIFEDIPNE" not in names


def test_unparseable_row_is_dropped(payload):
    """`Risperidone Quetiapine Quetiapine` 是三個藥名黏在一起且重複，
    對不出「這一列講的是哪一個藥」，整列丟棄而不是猜一個。"""
    names = {p["western"] for p in payload["pairs"]}
    assert not any("QUETIAPINE QUETIAPINE" in n for n in names)


def test_pairs_are_unique_and_sorted(payload):
    keys = [(p["tcm"], p["western"]) for p in payload["pairs"]]
    assert len(keys) == len(set(keys))
    assert keys == sorted(keys)


def test_readme_records_the_source_restrictions(payload):
    """來源站自述「僅供藥師參考」「不含西藥對西藥」，那兩項限制必須跟著資料
    一起保留，否則會誤用。"""
    readme = "".join(payload["_readme"])
    assert "僅供藥師參考" in readme
    assert "不含西藥對西藥" in readme
