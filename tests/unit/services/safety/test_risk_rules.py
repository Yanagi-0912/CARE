import pytest

from app.models.safety import DrugMention
from app.services.medication.drug_catalog_service import (
    DrugCatalogEntry,
    DrugCatalogService,
)
from app.services.safety.risk_rules import (
    DISPENSED_PACKAGE_MARKERS,
    assess,
    detect_foreign_scripts,
    looks_drug_related,
    normalize_drug_key,
)


@pytest.fixture
def catalog():
    """小型固定藥證庫。真實資料集有十一萬筆，測試不碰檔案系統。"""
    return DrugCatalogService(
        [
            DrugCatalogEntry(
                license_number="衛署藥輸字第025431號", name_zh="合利他命 強效錠"
            ),
            DrugCatalogEntry(
                license_number="衛署藥製字第012345號",
                name_zh="普拿疼錠500毫克",
                name_en="PANADOL TABLETS 500MG",
            ),
        ],
        threshold=0.88,
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("この薬は日本で買いました", ["ja"]),
        ("アリナミン EX PLUS", ["ja"]),
        ("LIPITOR 10mg", []),
        ("普拿疼錠500毫克", []),
        ("타이레놀 500mg", ["ko"]),
        ("พาราเซตามอล", ["th"]),
        ("", []),
    ],
    ids=[
        "平假名",
        "片假名",
        "英文藥名不觸發",
        "純中文數字不觸發",
        "韓文",
        "泰文",
        "空字串",
    ],
)
def test_detect_foreign_scripts(text, expected):
    """字符集是字元層級的事實：中文藥證品名不可能含假名，日文包裝幾乎必然有。

    拉丁字母排除在外，否則核准藥證的英文品名（LIPITOR、PANADOL）會全面誤報。
    """
    assert detect_foreign_scripts(text) == expected


def test_detect_foreign_scripts_reports_each_script_once():
    assert detect_foreign_scripts("これはアリナミンです") == ["ja"]


# 五條判定規則的窮舉表。每一列都是 spec「風險判定為藥證庫與取得訊號的複合
# 結果」裡的一格，改動判定門檻必然打破這張表。
@pytest.mark.parametrize(
    "catalog_hit,channel,foreign_scripts,expected",
    [
        # 不明通路：不論藥證庫是否命中一律 high
        (True, "tv_shopping", [], "high"),
        (False, "tv_shopping", [], "high"),
        (True, "acquaintance", [], "high"),
        (False, "acquaintance", [], "high"),
        (True, "online_marketplace", [], "high"),
        (False, "online_marketplace", [], "high"),
        # 未命中 + 境外訊號
        (False, "unknown", ["ja"], "high"),
        (False, "overseas_personal", [], "high"),
        # 未命中 + 無訊號 → 只是俗稱或錯字的可能性更大
        (False, "unknown", [], "low"),
        (False, "medical_institution", [], "low"),
        (False, "licensed_pharmacy", [], "low"),
        # 命中 + 境外訊號 → 同名不同版本，合利他命 EX PLUS 落在這格
        (True, "unknown", ["ja"], "high"),
        (True, "overseas_personal", [], "high"),
        # 命中 + 無訊號 → 台灣核准藥、正常通路，不介入
        (True, "unknown", [], "none"),
        (True, "licensed_pharmacy", [], "none"),
    ],
)
def test_assess_matrix(catalog_hit, channel, foreign_scripts, expected):
    mention = DrugMention(
        raw_name="某藥", channel=channel, catalog_hit=catalog_hit
    )

    assert assess(mention, foreign_scripts) == expected


def test_assess_flags_approved_name_with_japanese_packaging():
    """指標案例：「合利他命強効錠 EX PLUS」含容比對會命中我國核准的同名藥證。

    單看藥證庫會判成安全，正是最該攔下的境外代購案例。
    """
    mention = DrugMention(raw_name="合利他命強効錠 EX PLUS", catalog_hit=True)

    assert assess(mention, ["ja"]) == "high"


def test_assess_silences_low_on_dispensed_package():
    """藥袋 OCR 常有三到五個藥名，只要一個未命中就會落到 low。

    使用者剛拍的就是包裝，再推播「可以拍一下包裝嗎」是最常見的誤報。
    """
    mention = DrugMention(
        raw_name="某罕見藥",
        catalog_hit=False,
        dispensed_package_markers=list(DISPENSED_PACKAGE_MARKERS),
    )

    assert assess(mention, []) == "none"


def test_assess_keeps_low_when_dispensed_markers_incomplete():
    """法定必載欄位只出現一部分，不足以證明這是合法調劑包裝。"""
    mention = DrugMention(
        raw_name="某罕見藥",
        catalog_hit=False,
        dispensed_package_markers=["patient_name"],
    )

    assert assess(mention, []) == "low"


def test_assess_dispensed_package_does_not_suppress_high():
    """台灣的合法調劑藥袋不會帶外文字符集訊號；真的同時出現就值得通報。"""
    mention = DrugMention(
        raw_name="某藥",
        catalog_hit=True,
        dispensed_package_markers=list(DISPENSED_PACKAGE_MARKERS),
    )

    assert assess(mention, ["ja"]) == "high"


@pytest.mark.parametrize(
    "text",
    ["早安，今天天氣真好", "明天下午三點要去公園散步", "你吃飽了嗎"],
)
def test_looks_drug_related_blocks_everyday_messages(text, catalog):
    """絕大多數訊息與藥品無關，為每一則都呼叫模型是不必要的成本。"""
    assert looks_drug_related(text, catalog) is False


@pytest.mark.parametrize(
    "text",
    [
        "我朋友從日本代購的",
        "這罐保健食品可以每天吃嗎",
        "醫生開的膠囊還要吃幾天",
    ],
)
def test_looks_drug_related_passes_keyword_messages(text, catalog):
    assert looks_drug_related(text, catalog) is True


def test_looks_drug_related_passes_bare_catalog_name(catalog):
    """句子裡沒有任何關鍵詞，只有一個藥證庫查得到的藥名。"""
    assert looks_drug_related("PANADOL TABLETS 500MG 一次可以幾顆", catalog) is True


def test_looks_drug_related_survives_empty_catalog():
    """藥證庫缺席時仍能靠關鍵詞運作，SHALL NOT 拋例外。"""
    empty = DrugCatalogService([], threshold=0.88)

    assert looks_drug_related("這個藥還要吃嗎", empty) is True
    assert looks_drug_related("今天天氣真好", empty) is False


def test_normalize_drug_key_folds_spacing_and_case():
    """節流以正規化後的字串比對，同一個藥的不同寫法要落在同一筆。"""
    assert normalize_drug_key("合利他命 EX PLUS") == normalize_drug_key(
        "合利他命EX plus"
    )


def test_normalize_drug_key_handles_empty_name():
    assert normalize_drug_key("") == ""
