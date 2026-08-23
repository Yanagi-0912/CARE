"""仿單適應症服務：查詢、降級與比對三態。

比對的三態不是風格選擇：`unchecked`（沒有依據可比）與 `unrelated`（比過了
而且對不上）混為一談，會讓所有沒印適應症的藥袋全部被標記——而適應症不是
藥袋的法定必載欄位，缺席是常態。這份測試把那條界線釘住。

服務一律以建構子餵固定資料集（不碰檔案系統、不 monkey patch）。
"""

import json

import pytest

from app.services.medication.drug_indication_service import (
    DrugIndication,
    DrugIndicationService,
)

ENTRIES = {
    # 原文短、不需要摘要
    "衛署藥製字第000001號": {"text": "緩解便祕。", "summary": ""},
    # 原文長、有合格摘要
    "衛署藥製字第000002號": {
        "text": "1.本態性高血壓。2.治療左心室射出分率≦40%之心臟衰竭病患。",
        "summary": "高血壓、心臟衰竭",
    },
    # 全英文原文：去除非中日韓字元後湊不出 gram
    "衛署藥輸字第000003號": {"text": "Bacterial conjunctivitis.", "summary": ""},
}


@pytest.fixture()
def service() -> DrugIndicationService:
    return DrugIndicationService(ENTRIES)


# ── 查詢與降級 ──────────────────────────────────────────────────────


def test_lookup_returns_entry(service):
    found = service.lookup("衛署藥製字第000001號")
    assert isinstance(found, DrugIndication)
    assert found.text == "緩解便祕。"


def test_lookup_without_license_returns_none(service):
    """證號未確定時一律回 None——與『證號不確定時不得顯示藥丸照片』同一條邊界。"""
    assert service.lookup(None) is None
    assert service.lookup("") is None


def test_display_text_prefers_summary_then_falls_back(service):
    assert service.lookup("衛署藥製字第000002號").display_text == "高血壓、心臟衰竭"
    # 摘要為空（不需要摘要或產不出合格摘要）時退回原文，不是不顯示
    assert service.lookup("衛署藥製字第000001號").display_text == "緩解便祕。"


def test_entries_without_text_are_dropped():
    """沒有原文的條目留著只會讓呈現面多一個永遠是空的區塊。"""
    service = DrugIndicationService({"A": {"text": "", "summary": "x"}, "B": None})
    assert service.is_empty


def test_missing_file_degrades_to_empty_service(tmp_path):
    """檔案缺席不得讓應用啟動失敗，退化成『查無仿單』即可。"""
    service = DrugIndicationService.load_from_path(str(tmp_path / "nope.json"))
    assert service.is_empty
    assert service.lookup("衛署藥製字第000001號") is None
    assert service.compare("便祕", "衛署藥製字第000001號") == "unchecked"


def test_corrupt_file_degrades_to_empty_service(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{ 這不是 JSON", encoding="utf-8")
    assert DrugIndicationService.load_from_path(str(broken)).is_empty


def test_non_dict_payload_degrades_to_empty_service(tmp_path):
    """產出物格式改成陣列（例如誤用 catalog 的形狀）時同樣安全降級。"""
    as_list = tmp_path / "list.json"
    as_list.write_text(json.dumps([{"license_number": "A"}]), encoding="utf-8")
    assert DrugIndicationService.load_from_path(str(as_list)).is_empty


def test_load_from_path_reads_committed_shape(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(ENTRIES, ensure_ascii=False), encoding="utf-8")
    service = DrugIndicationService.load_from_path(str(good))
    assert not service.is_empty
    assert service.lookup("衛署藥製字第000002號").summary == "高血壓、心臟衰竭"


# ── 比對三態 ────────────────────────────────────────────────────────


def test_compare_consistent_when_grams_overlap(service):
    """藥袋印短語、仿單是長文，靠共同的醫學名詞字元重疊命中。"""
    assert service.compare("高血壓", "衛署藥製字第000002號") == "consistent"


def test_compare_unrelated_when_no_overlap(service):
    """spec scenario：完全不相干。"""
    assert service.compare("緩解便祕", "衛署藥製字第000002號") == "unrelated"


@pytest.mark.parametrize("bag", [None, "", "   "])
def test_compare_unchecked_when_bag_has_no_indication(service, bag):
    """spec scenario：藥袋沒有適應症。缺席不得記為不相干。"""
    assert service.compare(bag, "衛署藥製字第000002號") == "unchecked"


@pytest.mark.parametrize("license_number", [None, ""])
def test_compare_unchecked_when_license_undetermined(service, license_number):
    """spec scenario：證號未確定。"""
    assert service.compare("高血壓", license_number) == "unchecked"


def test_compare_unchecked_when_license_not_in_catalog(service):
    assert service.compare("高血壓", "查無此證號") == "unchecked"


def test_compare_unchecked_when_either_side_has_no_grams(service):
    """任一側去除英文與停用字後湊不出 gram，等於沒有可比的內容。

    純英文的仿單（菌株學名那類）與只由停用字組成的藥袋字串都屬此類——
    這種情況判成 unrelated 會是純粹的誤判。
    """
    assert service.compare("結膜炎", "衛署藥輸字第000003號") == "unchecked"
    assert service.compare("治療症狀", "衛署藥製字第000002號") == "unchecked"


def test_compare_ignores_stopwords_only_overlap():
    """只靠『治療』『症狀』這種到處都有的字重疊，不算相符。

    不排除停用字的話，任意兩段適應症幾乎都會重疊，這條規則就形同虛設。
    """
    service = DrugIndicationService(
        {"X": {"text": "治療症狀之緩解使用於便祕", "summary": ""}}
    )
    assert service.compare("治療症狀之改善", "X") == "unchecked"
