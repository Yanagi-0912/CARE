import io
import json
import zipfile

from scripts.build_drug_catalog import build_entries, read_dataset_zip


def _zip_bytes(payload, name="dataset.json") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, json.dumps(payload, ensure_ascii=False))
    return buffer.getvalue()


def test_read_dataset_zip_unwraps_the_archive():
    """兩個資料集的 export 端點回傳的是 ZIP，不是裸 JSON。"""
    payload = [{"許可證字號": "A", "中文品名": "某藥"}]

    assert read_dataset_zip(_zip_bytes(payload)) == payload


def test_build_entries_maps_licence_and_names():
    licences = [
        {
            "許可證字號": "衛署藥製字第000002號",
            "中文品名": "立普妥錠10毫克",
            "英文品名": "LIPITOR TABLETS 10MG",
        }
    ]

    entries = build_entries(licences, [])

    assert entries == [
        {
            "license_number": "衛署藥製字第000002號",
            "name_zh": "立普妥錠10毫克",
            "name_en": "LIPITOR TABLETS 10MG",
        }
    ]


def test_build_entries_skips_rows_without_licence_or_name():
    licences = [
        {"許可證字號": "", "中文品名": "沒有證號"},
        {"許可證字號": "有證號", "中文品名": "", "英文品名": ""},
        {"許可證字號": "有證號2", "中文品名": "有名字"},
    ]

    entries = build_entries(licences, [])

    assert [entry["license_number"] for entry in entries] == ["有證號2"]


def test_build_entries_tolerates_null_fields():
    """開放資料的欄位常是 null 而非空字串。"""
    licences = [
        {"許可證字號": "有證號", "中文品名": "有名字", "英文品名": None}
    ]

    entries = build_entries(licences, [])

    assert entries[0]["name_en"] == ""


def test_appearance_dataset_supplements_missing_licences():
    """外觀資料集補充許可證資料集沒有的品項，但不覆寫既有的。"""
    licences = [
        {"許可證字號": "L1", "中文品名": "許可證版名稱", "英文品名": "LICENCE NAME"}
    ]
    appearances = [
        {"許可證字號": "L1", "中文品名": "外觀版名稱", "英文品名": "SHAPE NAME"},
        {"許可證字號": "L2", "中文品名": "只在外觀資料集", "英文品名": "ONLY SHAPE"},
    ]

    entries = build_entries(licences, appearances)
    by_licence = {entry["license_number"]: entry for entry in entries}

    assert by_licence["L1"]["name_zh"] == "許可證版名稱"
    assert by_licence["L2"]["name_zh"] == "只在外觀資料集"


def test_build_entries_deduplicates_repeated_licence_numbers():
    licences = [
        {"許可證字號": "L1", "中文品名": "第一次"},
        {"許可證字號": "L1", "中文品名": "第二次"},
    ]

    entries = build_entries(licences, [])

    assert len(entries) == 1
    assert entries[0]["name_zh"] == "第一次"
