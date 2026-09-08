"""每日醫療消息卡的字面防線：純函式，不碰網路、資料庫或模型。

三件事：
1. `mentions_drug`——這則消息的文字裡有沒有真的出現這個藥名／成分。
2. `violates_output_guard`——這段要送給使用者的文字有沒有踩到用藥建議的紅線。
3. `has_usable_date` / `is_recent`——這則消息夠不夠新。

放在同一個模組是因為三者都是「便宜、確定、先於模型」的檢查，共同構成
design.md 決策 5 的前幾道防線。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from app.services.medication.drug_catalog_service import normalize_drug_name

# 正規化後短於此長度的鍵不予比對。單字元的鍵（來自「藥名只剩一個字」這類資料
# 瑕疵）會命中幾乎所有中文文字，讓前置篩選形同虛設。寧可漏掉這種鍵，也不要讓
# 它把整批雜訊送進 grader。
_MIN_KEY_LENGTH = 2

# 只由拉丁字母、數字與連字號構成的鍵，視為「有詞界」。
_LATIN_KEY = re.compile(r"^[A-Z0-9][A-Z0-9\-]*$")

# 停藥、換藥、調整劑量的建議。這是本功能唯一不得輸出的內容類別：消息卡是主動
# 推播，使用者沒有在問問題，任何行動建議都會被當成指示執行，而錯誤的停藥對
# 高齡使用者的傷害是實質且立即的。
#
# 這份清單是黑名單，永遠追不完——它是**第二層**防線，第一層是 grader 的 prompt
# 明確要求不得產生此類內容。第二層存在的理由是 prompt 可被繞過，字串比對不會。
# 漏接的方向是「該擋的沒擋」，因此新增時只加明確的、不加模稜兩可的字眼。
FORBIDDEN_ADVICE_PATTERNS: tuple[str, ...] = (
    "停藥",
    "停止服用",
    "停止使用",
    "不要再吃",
    "不要再服用",
    "改吃",
    "換藥",
    "自行調整",
    "調整劑量",
    "減量",
    "加量",
    "增加劑量",
    "減少劑量",
)

# 明顯不是日期的佔位字串。判定「有沒有可用的日期」時，這些等同於沒有。
_NON_DATE_PLACEHOLDERS: frozenset[str] = frozenset({"不詳", "未提供", "無", "-", "N/A"})

_DATE_PATTERN = re.compile(r"^(\d{2,4})[-/.](\d{1,2})[-/.](\d{1,2})$")

# 民國年與西元年的界線。三位數（含）以下一律當成民國年——西元年不可能是三位數，
# 而食藥署與衛福部的頁面普遍以民國年呈現（115-09-01）。
_ROC_YEAR_OFFSET = 1911
_ROC_YEAR_MAX = 999

# 允許的未來偏差。時區換算與頁面時差可能讓發布日看起來早一天；超過這個範圍
# 代表日期抽錯了欄位（抓到了「有效期限」之類），不該被當成最新消息。
_FUTURE_TOLERANCE_DAYS = 1


def mentions_drug(text: str, drug_key: str) -> bool:
    """這段文字裡有沒有出現這個藥名／成分。

    **這是為 recall 而設的前置篩選，不是精確判定。** 它的職責是在花掉抓取與
    LLM 成本之前，擋掉大量與這個藥無關的搜尋結果；「這則消息是不是真的在講
    這個藥」由 grader 的 `is_about_this_drug` 回答。

    **已知限制**：中文沒有詞界，因此「胃能錠」會命中「欲胃能錠」——那是別的藥。
    這裡刻意不修：修它的正確做法是引入藥證庫做最長匹配（比照 drug-appearance
    spec 的反向含容規則），而那會讓這個模組從純函式變成需要載入 15.9 MB 索引的
    服務，代價與收益不相稱——grader 看得到完整內文，分得出這兩者不同。

    拉丁字母的鍵則套用詞界比對：拉丁文字**有**詞界，可以精確要求完整詞，
    `ACID` 因此不會命中 `ACIDOPHILUS`。這個中日韓／拉丁的不對稱與
    `app/services/safety/risk_rules.py` 區分假名與拉丁字母的理由相同——
    對字元層級的事實，該用什麼規則由文字系統本身決定。
    """
    key = normalize_drug_name(drug_key)
    if len(key) < _MIN_KEY_LENGTH:
        return False

    haystack = normalize_drug_name(text)
    if not haystack:
        return False

    if _LATIN_KEY.match(key):
        return re.search(rf"(?<![A-Z0-9]){re.escape(key)}(?![A-Z0-9])", haystack) is not None

    return key in haystack


def violates_output_guard(text: str) -> bool:
    """這段文字是否含有不得推播的用藥建議。

    命中時呼叫端 SHALL 整則丟棄，SHALL NOT 嘗試改寫——改寫等於讓模型再賭一次，
    而這道防線存在的前提正是「不能相信模型會自己守住」。
    """
    if not text:
        return False
    return any(pattern in text for pattern in FORBIDDEN_ADVICE_PATTERNS)


def has_usable_date(published_at: str | None) -> bool:
    """有沒有一個解析得出來的發布日。

    抽不到日期的消息不得進入 Tier 1（design.md 決策 5 第 4 道防線）。gov.tw 的
    頁面日期位置不一致，抽取本來就會失敗；缺席時的預設必須是排除而非放行，
    否則「不知道多舊」的消息會混進「近期警訊」裡。
    """
    return _parse_date(published_at) is not None


def is_recent(published_at: str | None, today: str, max_age_days: int) -> bool:
    """這則消息是否在 `today` 往前 `max_age_days` 天的範圍內。

    解析不出來一律回 False——與 `has_usable_date` 同一個方向：缺資料時預設排除。
    發布日落在未來超過 `_FUTURE_TOLERANCE_DAYS` 天也回 False，那代表抽到了別的
    欄位。
    """
    published = _parse_date(published_at)
    reference = _parse_date(today)
    if published is None or reference is None:
        return False
    if published > reference + timedelta(days=_FUTURE_TOLERANCE_DAYS):
        return False
    return published >= reference - timedelta(days=max_age_days)


def _parse_date(value: str | None) -> date | None:
    """把 `2026-08-30` 或民國年的 `115-09-01` 解析成 date；失敗回 None。"""
    if not value:
        return None
    cleaned = value.strip()
    if not cleaned or cleaned.upper() in _NON_DATE_PLACEHOLDERS:
        return None

    match = _DATE_PATTERN.match(cleaned)
    if match is None:
        return None

    year, month, day = (int(part) for part in match.groups())
    if year <= _ROC_YEAR_MAX:
        year += _ROC_YEAR_OFFSET
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None
