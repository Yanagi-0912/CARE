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
            "image_url": "",
            "shape": "",
            "color": "",
            "score_line": "",
            "mark_one": "",
            "mark_two": "",
            "size": "",
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


# ── 外觀欄位 ──────────────────────────────────────────────────────────
#
# 外觀資料集提供藥丸照片與形狀／顏色／刻痕／標註／尺寸。這些欄位的附掛
# 規則跟品名的補充規則各自獨立：不論這張證號的品名最終取自哪個資料集，
# 只要外觀資料集有對應紀錄就要把外觀欄位貼上去；沒有外觀記錄的證號則
# 全部留空字串（不是 None、不是缺欄位）。


def test_appearance_fields_map_onto_matching_licence():
    licences = [{"許可證字號": "L1", "中文品名": "某藥", "英文品名": "SOME DRUG"}]
    appearances = [
        {
            "許可證字號": "L1",
            "中文品名": "某藥外觀版",
            "外觀圖檔連結": "https://mcp.fda.gov.tw/some.jpg",
            "形狀": "圓形",
            "顏色": "白色",
            "刻痕": "無",
            "標註一": "PBF",
            "標註二": "436",
            "外觀尺寸": "8mm",
        }
    ]

    entries = build_entries(licences, appearances)

    assert entries == [
        {
            "license_number": "L1",
            "name_zh": "某藥",
            "name_en": "SOME DRUG",
            "image_url": "https://mcp.fda.gov.tw/some.jpg",
            "shape": "圓形",
            "color": "白色",
            "score_line": "無",
            "mark_one": "PBF",
            "mark_two": "436",
            "size": "8mm",
        }
    ]


def test_missing_appearance_record_leaves_appearance_fields_empty():
    """無外觀記錄的藥證，外觀欄位一律留空——不是缺鍵，是空字串。"""
    licences = [{"許可證字號": "L1", "中文品名": "某藥"}]

    entries = build_entries(licences, [])

    entry = entries[0]
    assert entry["image_url"] == ""
    assert entry["shape"] == ""
    assert entry["color"] == ""
    assert entry["score_line"] == ""
    assert entry["mark_one"] == ""
    assert entry["mark_two"] == ""
    assert entry["size"] == ""


def test_appearance_fields_attach_without_changing_which_name_wins():
    """既有補充規則：許可證資料集有的品名不被外觀資料集覆寫。這裡確認
    外觀欄位仍正確附掛到同一張證號上——名稱誰贏，跟外觀欄位貼不貼是
    兩件互不相干的事，不能因為加了外觀欄位就悄悄改動名稱的勝負規則。"""
    licences = [
        {"許可證字號": "L1", "中文品名": "許可證版名稱", "英文品名": "LICENCE NAME"}
    ]
    appearances = [
        {
            "許可證字號": "L1",
            "中文品名": "外觀版名稱",
            "英文品名": "SHAPE NAME",
            "形狀": "圓形",
            "顏色": "白色",
        },
        {"許可證字號": "L2", "中文品名": "只在外觀資料集", "形狀": "橢圓形"},
    ]

    entries = build_entries(licences, appearances)
    by_licence = {entry["license_number"]: entry for entry in entries}

    assert by_licence["L1"]["name_zh"] == "許可證版名稱"  # 名稱仍是許可證版贏
    assert by_licence["L1"]["shape"] == "圓形"  # 但外觀欄位要附掛上去
    assert by_licence["L2"]["name_zh"] == "只在外觀資料集"
    assert by_licence["L2"]["shape"] == "橢圓形"


def test_appearance_field_null_becomes_empty_string():
    """實測欄位常見 null（非 None 字串）；顏色可能是 `黃;;;白` 這種混亂
    值，刻痕常是字面上的 `無`——一律原樣帶過，不在建置腳本做正規化。"""
    licences = [{"許可證字號": "L1", "中文品名": "某藥"}]
    appearances = [
        {
            "許可證字號": "L1",
            "中文品名": "某藥",
            "外觀圖檔連結": None,
            "形狀": None,
            "顏色": "黃;;;白",
            "刻痕": "無",
            "標註一": None,
            "標註二": None,
            "外觀尺寸": None,
        }
    ]

    entries = build_entries(licences, appearances)
    entry = entries[0]

    assert entry["image_url"] == ""
    assert entry["shape"] == ""
    assert entry["color"] == "黃;;;白"
    assert entry["score_line"] == "無"
    assert entry["mark_one"] == ""
    assert entry["mark_two"] == ""
    assert entry["size"] == ""
