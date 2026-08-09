import json

from app.services.medication.drug_catalog_service import (
    DrugCatalogEntry,
    DrugCatalogService,
)

AMLODIPINE = DrugCatalogEntry(
    license_number="衛署藥製字第000001號",
    name_zh='"福元"脈優錠5毫克',
    name_en='AMLODIPINE TABLETS 5MG "F.Y."',
)
ATORVASTATIN = DrugCatalogEntry(
    license_number="衛署藥製字第000002號",
    name_zh="立普妥錠10毫克",
    name_en="LIPITOR TABLETS 10MG",
)


def _service(threshold: float = 0.88) -> DrugCatalogService:
    return DrugCatalogService([AMLODIPINE, ATORVASTATIN], threshold=threshold)


def test_exact_chinese_name_matches():
    match = _service().match("立普妥錠10毫克")

    assert match is not None
    assert match.license_number == "衛署藥製字第000002號"
    assert match.name_zh == "立普妥錠10毫克"


def test_english_name_matches():
    match = _service().match("LIPITOR TABLETS 10MG")

    assert match is not None
    assert match.license_number == "衛署藥製字第000002號"


def test_manufacturer_prefix_is_ignored():
    """藥袋通常只印藥名，藥證上的品名卻帶著引號包住的廠商前綴。"""
    match = _service().match("脈優錠5毫克")

    assert match is not None
    assert match.license_number == "衛署藥製字第000001號"


def test_fullwidth_and_whitespace_differences_are_ignored():
    match = _service().match("立普妥錠 １０毫克")

    assert match is not None
    assert match.license_number == "衛署藥製字第000002號"


def test_unrelated_name_does_not_match():
    assert _service().match("銀杏葉萃取物膠囊") is None


def test_similar_name_below_threshold_does_not_match():
    """視覺模型把藥名讀成形近字時，比對必須落空而不是勉強命中。"""
    assert _service(threshold=0.99).match("脈定錠5毫克") is None


def test_same_name_matches_when_threshold_is_lowered():
    """對照上一個測試：落空來自門檻，不是因為比對根本沒運作。"""
    match = _service(threshold=0.5).match("脈定錠5毫克")

    assert match is not None
    assert match.license_number == "衛署藥製字第000001號"


def test_empty_name_does_not_match():
    assert _service().match("") is None


def test_empty_catalog_matches_nothing():
    assert DrugCatalogService([], threshold=0.88).match("立普妥錠10毫克") is None


def test_load_from_path_reads_entries(tmp_path):
    path = tmp_path / "drug_catalog.json"
    path.write_text(
        json.dumps(
            [
                {
                    "license_number": "衛署藥製字第000002號",
                    "name_zh": "立普妥錠10毫克",
                    "name_en": "LIPITOR TABLETS 10MG",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    service = DrugCatalogService.load_from_path(str(path), threshold=0.88)

    assert service.match("立普妥錠10毫克") is not None


def test_load_from_path_tolerates_missing_file(tmp_path):
    """藥證庫缺席不得讓應用啟動失敗；改為所有藥名都比對不到（降為低信心）。"""
    service = DrugCatalogService.load_from_path(
        str(tmp_path / "does_not_exist.json"), threshold=0.88
    )

    assert service.is_empty is True
    assert service.match("立普妥錠10毫克") is None


def test_load_from_path_tolerates_malformed_file(tmp_path):
    path = tmp_path / "drug_catalog.json"
    path.write_text("{not json", encoding="utf-8")

    service = DrugCatalogService.load_from_path(str(path), threshold=0.88)

    assert service.is_empty is True


def test_load_from_path_warns_when_the_file_parses_but_has_no_entries(tmp_path, caplog):
    """欄位名稱對不上 FDA 資料集時，載入不會拋例外——只會得到一個條目數為 0
    的服務，之後每份草稿都悄悄降為低信心。這種情況必須大聲記錄，不能只在
    「不存在」或「格式錯誤」時才出聲。"""
    path = tmp_path / "drug_catalog.json"
    path.write_text("[]", encoding="utf-8")

    with caplog.at_level("WARNING"):
        service = DrugCatalogService.load_from_path(str(path), threshold=0.88)

    assert service.is_empty is True
    assert any("條目數為 0" in record.message for record in caplog.records)


def test_load_from_path_logs_entry_count_on_success(tmp_path, caplog):
    path = tmp_path / "drug_catalog.json"
    path.write_text(
        json.dumps(
            [
                {
                    "license_number": "衛署藥製字第000002號",
                    "name_zh": "立普妥錠10毫克",
                    "name_en": "LIPITOR TABLETS 10MG",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with caplog.at_level("INFO"):
        service = DrugCatalogService.load_from_path(str(path), threshold=0.88)

    assert service.is_empty is False
    assert any("共 1 筆條目" in record.message for record in caplog.records)
