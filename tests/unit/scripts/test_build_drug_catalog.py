import hashlib
import io
import json
import shutil
import stat
import zipfile

import pytest

from scripts.build_drug_catalog import (
    _require_magick,
    build_entries,
    image_fetch_targets,
    pending_image_targets,
    read_dataset_zip,
    thumbnail_filename,
)


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


# ── 藥丸照片抓取：純邏輯部分（--fetch-images，見 design.md 決策 2、3、6）──
#
# 抓圖與縮圖本身需要網路與 ImageMagick，不在單元測試範圍內；但「檔名怎麼
# 算」與「哪些該跳過」是純邏輯，刻意拆成獨立函式讓這裡直接測，不必對
# urllib 或 subprocess 做 monkeypatch。


def test_thumbnail_filename_is_sha256_prefix_of_license_number():
    license_number = "衛署藥製字第000002號"
    expected = hashlib.sha256(license_number.encode("utf-8")).hexdigest()[:16] + ".jpg"

    assert thumbnail_filename(license_number) == expected


def test_thumbnail_filename_is_deterministic_and_distinct_per_licence():
    assert thumbnail_filename("A") == thumbnail_filename("A")
    assert thumbnail_filename("A") != thumbnail_filename("B")


def test_image_fetch_targets_keeps_only_entries_with_licence_and_http_image():
    entries = [
        {"license_number": "L1", "image_url": "https://mcp.fda.gov.tw/a.jpg"},
        {"license_number": "L2", "image_url": ""},  # 無外觀記錄
        {"license_number": "", "image_url": "https://mcp.fda.gov.tw/c.jpg"},  # 理論上不該發生，仍要擋
        {"license_number": "L4", "image_url": "not-a-url"},
    ]

    assert image_fetch_targets(entries) == [("L1", "https://mcp.fda.gov.tw/a.jpg")]


def test_pending_image_targets_skips_files_that_already_exist(tmp_path):
    """已存在的縮圖不重抓——這是抓取可中斷續跑的關鍵，見 build_drug_catalog
    模組文件。用真實的暫存目錄驗證，不必 monkeypatch 檔案系統。"""
    (tmp_path / thumbnail_filename("L1")).write_bytes(b"fake-jpeg")
    targets = [("L1", "https://example.test/1.jpg"), ("L2", "https://example.test/2.jpg")]

    pending = pending_image_targets(targets, str(tmp_path))

    assert [license_number for license_number, _, _ in pending] == ["L2"]


def test_pending_image_targets_returns_destination_path_under_image_dir(tmp_path):
    targets = [("L1", "https://example.test/1.jpg")]

    pending = pending_image_targets(targets, str(tmp_path))

    assert pending == [
        ("L1", "https://example.test/1.jpg", str(tmp_path / thumbnail_filename("L1")))
    ]


# ── magick 前置檢查（fetch_images 的第一件事，見 build_drug_catalog 模組文件）──
#
# 沒有這道檢查時，缺少 magick 只會在逐一轉檔時才被發現，但下載在轉檔之前
# 就已發生——六千多次對政府主機的請求全部白費才失敗。`_require_magick`
# 接受一個 `path` 參數覆寫要搜尋的目錄，讓「PATH 上沒有 magick」不必真的
# 更動使用者的 PATH 環境變數就能重現，跟本檔案其他測試一樣只用真實的
# tmp_path，不 monkeypatch。


def test_require_magick_raises_clearly_when_binary_is_absent(tmp_path):
    """搜尋目錄裡沒有 magick 時要在任何下載前就失敗，訊息要點名缺的是誰。"""
    with pytest.raises(RuntimeError, match="magick"):
        _require_magick(path=str(tmp_path))  # 空目錄，什麼執行檔都沒有


def test_require_magick_passes_when_binary_is_present(tmp_path):
    fake_magick = tmp_path / "magick"
    fake_magick.write_text("#!/bin/sh\nexit 0\n")
    fake_magick.chmod(fake_magick.stat().st_mode | stat.S_IEXEC)

    _require_magick(path=str(tmp_path))  # 不丟例外即為通過


def test_require_magick_uses_real_path_by_default():
    """不帶 path 參數時要吃真正的 PATH 環境變數——這是 fetch_images 呼叫的
    形式。開發機與 CI 對 --fetch-images 的假設不同（design.md 決策 3：
    抓圖不在一般開發或 CI 路徑上），所以這裡不斷言結果，只確認呼叫時不需要
    額外參數、且行為對齊 shutil.which 對真實 PATH 的判斷。"""
    if shutil.which("magick") is None:
        with pytest.raises(RuntimeError, match="magick"):
            _require_magick()
    else:
        _require_magick()  # 不丟例外
