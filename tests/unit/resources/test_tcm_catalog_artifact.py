"""釘住實際出貨的中藥庫產出物。

與 test_drug_catalog_artifact.py 同一個目的：建表腳本正確不代表 repo 裡那份
檔案是正確的——它可能是舊版本、可能重建失敗只寫了一半。
"""

import json
from pathlib import Path

import pytest

CATALOG_PATH = Path(__file__).resolve().parents[3] / "resources" / "tcm_catalog.json"


@pytest.fixture(scope="module")
def catalog() -> list:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def test_contains_all_two_hundred_standard_formulas(catalog):
    """基準方是部訂的 200 方，少一方就是抓取中斷。"""
    assert sum(1 for e in catalog if e["kind"] == "formula") == 200


def test_contains_the_herbal_pharmacopeia_items(catalog):
    """臺灣中藥典第四版：385 種中藥材 ＋ 9 種濃縮製劑。"""
    kinds = [e["kind"] for e in catalog]
    assert kinds.count("herb") == 385
    assert kinds.count("concentrated") == 9


def test_every_entry_has_a_composition(catalog):
    """沒有組成的條目對比對沒有貢獻——這是整個中藥庫存在的理由。"""
    assert all(e["herbs"] for e in catalog)


def test_ephedra_formulas_are_present(catalog):
    """麻黃是 DL-METHYLEPHEDRINE 的天然來源，而後者是成分重複白名單第 3 項。
    「綜合感冒藥 ＋ 葛根湯」正是這個中藥庫要補上的那一格。"""
    with_ephedra = {e["name_zh"] for e in catalog if "麻黃" in e["herbs"]}

    assert {"葛根湯", "小青龍湯", "麻黃湯"} <= with_ephedra


def test_licorice_is_the_most_common_herb(catalog):
    """甘草配利尿劑、降血壓藥會低血鉀，而它出現在超過半數的基準方裡。"""
    formulas = [e for e in catalog if e["kind"] == "formula"]
    with_licorice = [e for e in formulas if {"甘草", "炙甘草"} & set(e["herbs"])]

    assert len(with_licorice) > len(formulas) / 2


def test_codes_are_unique_and_sorted(catalog):
    codes = [e["code"] for e in catalog]
    assert len(codes) == len(set(codes))
    assert codes == sorted(codes)
