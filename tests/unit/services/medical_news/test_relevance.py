import pytest

from app.services.medical_news.relevance import (
    FORBIDDEN_ADVICE_PATTERNS,
    has_usable_date,
    is_recent,
    mentions_drug,
    violates_output_guard,
)


# ── 字面比對（前置篩選）────────────────────────────────────────────


def test_mentions_drug_matches_after_normalization():
    """藥袋短名出現在公告內文即命中。全形／空白／大小寫差異不得造成漏接。"""
    assert mentions_drug("食藥署公告 普拿疼 錠劑回收", "普拿疼") is True
    assert mentions_drug("ＡＣＥＴＡＭＩＮＯＰＨＥＮ 相關公告", "acetaminophen") is True


def test_mentions_drug_rejects_unrelated_text():
    assert mentions_drug("食藥署公告冠脂妥回收", "普拿疼") is False


def test_mentions_drug_requires_two_chars():
    """單字元的鍵會命中幾乎所有文字，前置篩選等於失效。

    這種鍵來自資料瑕疵（藥名只剩一個字），寧可漏掉也不要讓它把整批雜訊放進來。
    """
    assert mentions_drug("這是一段包含胃字的公告", "胃") is False


def test_mentions_drug_uses_word_boundary_for_latin():
    """拉丁字母有詞界，可以要求完整詞比對——ACID 不該命中 ACIDOPHILUS。

    中日韓文字沒有詞界，因此不套用同一條規則（見下一個測試記錄的已知限制）。
    這個不對稱與 app/services/safety/risk_rules.py 區分拉丁與假名的理由相同。
    """
    assert mentions_drug("含 ACIDOPHILUS 之製劑", "ACID") is False
    assert mentions_drug("含 ACID 之製劑", "ACID") is True


def test_mentions_drug_known_false_positive_for_cjk_substring():
    """**已知限制，刻意以測試記錄而非隱藏。**

    中文沒有詞界，因此「胃能錠」會命中「欲胃能錠」——這兩者是不同的藥。
    這裡不做修正：字面比對是為 recall 而設的前置篩選，它的職責是在花掉 LLM
    成本之前擋掉大量無關結果；精確度由 grader 的 is_about_this_drug 承擔，
    而 grader 看得到完整內文，分得出「欲胃能錠」不是「胃能錠」。

    若哪天要收緊這裡，正確做法是引入藥證庫做最長匹配（比照 drug-appearance
    spec 的反向含容規則），不是加關鍵字例外。
    """
    assert mentions_drug("欲胃能錠回收公告", "胃能錠") is True


def test_mentions_drug_handles_empty_inputs():
    assert mentions_drug("", "普拿疼") is False
    assert mentions_drug("公告", "") is False


# ── 輸出防線 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "建議停藥並回診",
        "請自行減量服用",
        "可以改吃別的廠牌",
        "請停止服用本藥",
        "民眾應自行調整劑量",
    ],
)
def test_violates_output_guard_catches_medication_advice(text):
    assert violates_output_guard(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "請與您的醫師或藥師確認",
        "食藥署公告某批號回收，已通知各醫療院所下架。",
        "本品之適應症為緩解疼痛。",
    ],
)
def test_violates_output_guard_allows_safe_text(text):
    assert violates_output_guard(text) is False


def test_forbidden_patterns_are_non_empty_and_unique():
    assert len(FORBIDDEN_ADVICE_PATTERNS) > 0
    assert len(set(FORBIDDEN_ADVICE_PATTERNS)) == len(FORBIDDEN_ADVICE_PATTERNS)


# ── 時效 ────────────────────────────────────────────────────────────


def test_has_usable_date_rejects_none_and_blank():
    assert has_usable_date(None) is False
    assert has_usable_date("") is False
    assert has_usable_date("   ") is False
    assert has_usable_date("不詳") is False


def test_has_usable_date_accepts_iso_date():
    assert has_usable_date("2026-08-30") is True


def test_has_usable_date_accepts_roc_date():
    """食藥署與衛福部的頁面常以民國年呈現（115-09-01）。"""
    assert has_usable_date("115-09-01") is True


def test_is_recent_within_threshold():
    assert is_recent("2026-08-30", today="2026-09-02", max_age_days=30) is True


def test_is_recent_excludes_beyond_threshold():
    assert is_recent("2026-06-01", today="2026-09-02", max_age_days=30) is False


def test_is_recent_normalizes_roc_year():
    assert is_recent("115-09-01", today="2026-09-02", max_age_days=30) is True


def test_is_recent_rejects_unparsable_date():
    """解析不出來即視為不夠新——缺資料時的預設必須是排除而非放行。"""
    assert is_recent("不詳", today="2026-09-02", max_age_days=30) is False


def test_is_recent_rejects_future_date_beyond_tolerance():
    """發布日在未來超過一天，代表日期抽錯了欄位，不該當成最新消息。"""
    assert is_recent("2027-01-01", today="2026-09-02", max_age_days=30) is False
