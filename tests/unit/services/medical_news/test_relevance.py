import pytest

from app.services.medical_news import relevance
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


# ── 政策新聞稿判定 ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "title",
    [
        "國健署攜手軍醫局 打造無菸健康戰力 國軍弟兄呼吸更清新、體能更給力",
        "戒菸專線青年大使出動！「年輕人影響年輕人」迎向無菸新世代",
        "試管嬰兒補助再加碼 支持不孕夫妻圓生育夢 9月1日起申請適用",
        "打造全民健康飲食生活 「全國社區營養成果展示活動」8月7日登場",
        "行政院院會審議通過「菸害防制法」部分條文修正草案",
        "國民健康署說明電子煙查緝執行情形及後續精進作為",
        "臺灣6家無菸醫院獲國際殊榮 守護國民呼吸健康",
        "消除C肝「台灣模式」 日內瓦論壇受國際高度讚嘆",
        "代謝防治助60萬人迎向健康 即日起加入「逆轉」活動再抽好禮",
        "2025「健康飲食實踐獎」頒獎典禮 表揚業界典範",
    ],
)
def test_policy_announcements_are_detected(title):
    """全部取自 `health_articles_chunks` 的真實標題（2026-09-04 量測樣本）。"""
    assert relevance.is_policy_announcement(title) is True


@pytest.mark.parametrize(
    "title",
    [
        "中元節採買提物　長者掌握3要訣防跌",
        "立秋已過暑氣未退　處暑防熱傷害不可鬆懈",
        "每月量一次腰圍！「黃阿瑪」陪你一起遠離代謝症候群！",
        "國健署教你「722」與「3C」血壓管理密碼 輕鬆護心過一夏",
        "端午防熱大作戰 慢性病患「三要訣」遠離熱傷害",
        "網傳「70歲長輩別做5項檢查」，是誇大檢查風險的誤導說法",
        "一般成人血壓標準為<120/80mmHg，未因年齡設有不同標準",
        "舊謠言重組，造謠蝦子配維生素、榴槤配酒會中毒",
        "美國藥品研究確實發現部分藥物過期仍有療效；但專家建議應妥善保存",
        "人工色素、苯甲酸鈉可能與過動症有關，但並非導致過動主因",
    ],
)
def test_genuine_health_education_is_not_flagged(title):
    """量測樣本裡 TFC 的 90 篇與食藥署闢謠專區的 5 篇零命中，這裡釘住其中幾則。"""
    assert relevance.is_policy_announcement(title) is False


def test_empty_title_is_not_a_policy_announcement():
    assert relevance.is_policy_announcement("") is False
    assert relevance.is_policy_announcement(None) is False


# ── 健康媒體過濾 ──────────────────────────────────────────────────────

from app.services.medical_news.relevance import (  # noqa: E402
    MEDIA_ALLOWED_CATEGORIES,
    is_allowed_media_article,
)


def test_media_category_is_an_allowlist():
    """沒見過的分類預設不收：封鎖清單漏列一個「性愛」，推出去的就是不該給長輩的卡。"""
    assert is_allowed_media_article("醫療", "健檢發現甲狀腺結節會癌變嗎？")
    for category in ("性愛", "退休力", "名人", "醫聲", "寵物", "ESG", "某個新分類", None, ""):
        assert not is_allowed_media_article(category, "健檢發現甲狀腺結節會癌變嗎？"), category


def test_covid_category_is_allowed():
    """樣本沒抽到，但它是導覽列上的頂層分類、內容是疫情衛教。"""
    assert "新冠肺炎" in MEDIA_ALLOWED_CATEGORIES


def test_media_noise_titles_are_blocked():
    """全部取自 2026-09-13 元氣網樣本的真實標題。"""
    for title in (
        "飯店入住別急著太早到 這時間Check-in更有機會免費升等房型",
        "冰箱壽命10至15年，為何你家8年就壞？維修師揭6個常見使用錯誤",
        "陽明山驚爆殺人棄屍 37歲男友起初還裝傻",
        "鬼月醫院不能說的禁忌！一張病床1年「送走近20人」",
        "違法吸金2.5億…昔「房仲金童」遭重判 卻驚傳因流感猝逝",
        "突破困境不用英雄 歐嘉隆台灣總經理帶隊打團體戰",
        "我的助聽人生／母親戴上助聽器 重新聽見我呼喚",
    ):
        assert not is_allowed_media_article("焦點", title), title


def test_words_deliberately_left_out_do_not_block_real_health_content():
    """「禁忌」「保險」「股市」「詐騙」刻意不收，因為會誤擋正是要推的內容。"""
    for title in (
        "降血壓藥的用藥禁忌 這3種食物別一起吃",
        "全民健康保險新制上路 慢性病處方箋怎麼領",
        "股市一震盪就失眠、心悸？醫見科技人焦慮求診增2成 一情況建議就醫",
        "長輩接到假藥商詐騙電話 藥師教3招辨別",
    ):
        assert is_allowed_media_article("醫療", title), title


def test_media_policy_announcements_are_blocked_too():
    """「誰辦了什麼」不分官方或媒體都不是衛教，沿用 is_policy_announcement。"""
    assert not is_allowed_media_article(
        "焦點", "成大醫院呼吸道疾病衛教週登場，氣喘、肺阻塞團隊攜手守護全齡呼吸健康")
