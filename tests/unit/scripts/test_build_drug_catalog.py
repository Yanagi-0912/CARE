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
            "drug_class": "",
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
            "drug_class": "",
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


# ── CLI 輸出路徑的邊界檢查 ──────────────────────────────────────────
#
# 這支腳本的三個輸出路徑（--output／--indication-output／--image-dir）先前
# 直接進 open()／os.makedirs()／os.replace()／os.remove()，沒有任何驗證。
# 帶錯的參數（不論是人手誤、CI 設定錯，或由 LLM 組出來的指令）可以讓它在
# 專案目錄外建檔、覆寫檔案，其中 --image-dir 還會寫進數千個檔案。
#
# 檢查刻意只放在 CLI 邊界：函式庫層仍接受任意路徑，測試才能用 tmp_path
# 直接驗證抓圖與寫檔邏輯（見上方那些測試）。


def test_resolve_output_path_accepts_path_inside_project():
    from scripts.build_drug_catalog import PROJECT_ROOT, resolve_output_path

    resolved = resolve_output_path("resources/drug_catalog.json", argument="--output")
    assert resolved.startswith(str(PROJECT_ROOT))
    assert resolved.endswith("drug_catalog.json")


def test_resolve_output_path_normalises_relative_segments():
    """`a/../b` 這種寫法本身合法，收斂後仍在專案內就該放行。"""
    from scripts.build_drug_catalog import PROJECT_ROOT, resolve_output_path

    resolved = resolve_output_path("resources/../resources/x.json", argument="--output")
    assert resolved == str(PROJECT_ROOT / "resources" / "x.json")


@pytest.mark.parametrize(
    "raw",
    [
        "../escaped.json",
        "../../../../tmp/escaped.json",
        "/tmp/escaped.json",
        "/",
    ],
)
def test_resolve_output_path_rejects_paths_outside_project(raw):
    from scripts.build_drug_catalog import resolve_output_path

    with pytest.raises(ValueError) as excinfo:
        resolve_output_path(raw, argument="--output")
    # 錯誤訊息要指名是哪個參數，否則帶三個路徑參數時不知道是哪一個被擋
    assert "--output" in str(excinfo.value)


def test_resolve_output_path_rejects_symlink_escaping_project(tmp_path):
    """符號連結是繞過字串比對的經典手法：路徑字面上在專案內，實際指向外面。

    因此驗證必須在 resolve()（會跟隨符號連結）之後做，不能只比對字串前綴。
    """
    from scripts.build_drug_catalog import PROJECT_ROOT, resolve_output_path

    link = PROJECT_ROOT / "resources" / "_symlink_escape_test"
    outside = tmp_path / "outside"
    outside.mkdir()
    link.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(ValueError):
            resolve_output_path(
                "resources/_symlink_escape_test/x.json", argument="--output"
            )
    finally:
        link.unlink()


# ── 配額耗盡時提早中止 ──────────────────────────────────────────────
#
# 實測（12,174 筆的全量批次）撞到 Gemini 免費層每日 10,000 次的上限後，
# 批次仍繼續送出約 2,309 次請求，每一次都拿 429。那些請求不可能成功——
# 每日配額要等隔天才重置——只是白白拖慢收尾並繼續打對方的服務。


class _QuotaExhaustedModel:
    """前 N 次正常回覆，之後一律拋出配額耗盡。"""

    def __init__(self, ok_before: int):
        self.ok_before = ok_before
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        if self.calls > self.ok_before:
            raise RuntimeError(
                "Error calling model 'gemini-2.5-flash' (RESOURCE_EXHAUSTED): "
                "429 RESOURCE_EXHAUSTED. Quota exceeded for metric: "
                "generate_requests_per_model_per_day, limit: 10000"
            )

        class _Reply:
            content = "高血壓"

        return _Reply()


def test_is_quota_exhausted_recognises_resource_exhausted():
    from scripts.build_drug_catalog import is_quota_exhausted

    assert is_quota_exhausted(RuntimeError("429 RESOURCE_EXHAUSTED. Quota exceeded"))
    assert is_quota_exhausted(RuntimeError("... generate_requests_per_model_per_day ..."))


def test_is_quota_exhausted_ignores_other_failures():
    """逾時、連線中斷這類單筆失敗不該中止整批——它們下一筆可能就成功了。"""
    from scripts.build_drug_catalog import is_quota_exhausted

    assert not is_quota_exhausted(TimeoutError("read timeout"))
    assert not is_quota_exhausted(RuntimeError("500 INTERNAL"))
    assert not is_quota_exhausted(RuntimeError("400 INVALID_ARGUMENT"))


def test_summarize_stops_early_once_quota_is_exhausted():
    """配額耗盡後 SHALL NOT 再送出任何請求。

    剩下的目標維持空摘要，呈現面退回顯示原文；下次配額重置後重跑，
    build_indications 的冪等沿用會讓已完成的部分不必重算。
    """
    from scripts.build_drug_catalog import summarize_indications

    indications = {
        f"L{i}": {"text": "1.本態性高血壓。2.心臟衰竭。", "summary": "", "summary_of": "x"}
        for i in range(20)
    }
    targets = list(indications)
    model = _QuotaExhaustedModel(ok_before=5)

    stat = summarize_indications(indications, targets, max_chars=60, model=model)

    # 第 6 次拿到配額錯誤即停，不該打完 20 次
    assert model.calls < len(targets)
    assert stat["ok"] == 5
    assert stat["quota_exhausted"] is True
    # 已完成的保留，未處理的維持空字串（呈現面顯示原文）
    assert sum(1 for e in indications.values() if e["summary"]) == 5


# ── 藥品分級（藥事法第 8 條）────────────────────────────────────────
# 分級決定下游要不要問使用者「你還在吃嗎」：非處方藥在藥局買得到、通常短期
# 使用，而藥盒上不印療程天數，留白就會變成永久提醒。


class TestClassifyDrug:
    def test_prescription_categories(self):
        from scripts.build_drug_catalog import classify_drug

        for category in ("須由醫師處方使用", "限由醫師使用", "限由牙醫師使用",
                         "本藥須由醫師處方使用(限由皮膚科專科醫師使用)"):
            assert classify_drug(category) == "prescription", category

    def test_doctor_instructed_is_otc_not_prescription(self):
        """「須經醫師指示使用」是指示藥，不是處方藥——這是關鍵字比對會出錯的地方。

        它含「醫師」二字，任何「含醫師就算處方藥」的規則都會歸錯，而這一格
        線上有 5,842 筆。藥事法第 8 條把指示藥定義為「醫師藥師藥劑生指示藥品」，
        食藥署衛教也明確把這個標示歸為指示藥。
        """
        from scripts.build_drug_catalog import classify_drug

        assert classify_drug("須經醫師指示使用") == "otc_guided"
        assert classify_drug("醫師藥師藥劑生指示藥品") == "otc_guided"
        assert classify_drug("牙醫師指示使用") == "otc_guided"

    def test_over_the_counter_categories(self):
        from scripts.build_drug_catalog import classify_drug

        for category in ("成藥", "甲類成藥", "乙類成藥"):
            assert classify_drug(category) == "otc", category

    def test_non_medicine_categories(self):
        """製劑原料、空膠囊不是病人會拿在手上的成品藥，線上佔 18.6%。

        歸進任何一個藥品分級都是錯的——下游若把它們當成藥，會對根本不會出現在
        藥袋裡的東西提問。
        """
        from scripts.build_drug_catalog import classify_drug

        for category in ("製劑原料", "自用製劑原料", "原料藥", "空膠囊",
                         "調劑專用", "調劑專用製劑"):
            assert classify_drug(category) == "not_a_medicine", category

    def test_unknown_category_returns_empty_not_a_guess(self):
        """認不得就回空字串，不猜。

        上游若新增類別，猜成處方藥會讓下游少提醒，猜成成藥則會多問一次
        「還在吃嗎」。兩個方向都不好，不如讓下游看到空值自己決定。
        """
        from scripts.build_drug_catalog import classify_drug

        for category in ("未來的新類別", "", None, "   "):
            assert classify_drug(category) == ""

    def test_catalog_entry_carries_drug_class(self):
        from scripts.build_drug_catalog import build_entries

        entries = build_entries(
            [{"許可證字號": "衛部藥製字第000001號", "中文品名": "普拿疼",
              "英文品名": "PANADOL", "藥品類別": "醫師藥師藥劑生指示藥品"}],
            [],
        )

        assert entries[0]["drug_class"] == "otc_guided"

    def test_appearance_only_entry_has_empty_drug_class(self):
        """只出現在外觀資料集的品項沒有「藥品類別」欄，分級為空字串。"""
        from scripts.build_drug_catalog import build_entries

        entries = build_entries(
            [],
            [{"許可證字號": "衛部藥製字第000002號", "中文品名": "某藥",
              "英文品名": "SOME DRUG"}],
        )

        assert entries[0]["drug_class"] == ""
