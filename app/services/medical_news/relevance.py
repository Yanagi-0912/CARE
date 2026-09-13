"""每日醫療消息卡的字面防線：純函式，不碰網路、資料庫或模型。

四件事：
1. `mentions_drug`——這則消息的文字裡有沒有真的出現這個藥名／成分。
2. `violates_output_guard`——這段要送給使用者的文字有沒有踩到用藥建議的紅線。
3. `has_usable_date` / `is_recent`——這則消息夠不夠新。
4. `is_policy_announcement`——這篇是政策宣傳／活動新聞稿而不是衛教內容。

放在同一個模組是因為四者都是「便宜、確定、先於模型」的檢查，共同構成
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

# 政策宣傳與活動新聞稿的標題特徵。Tier 2 選材命中即整篇丟棄。
#
# 為什麼需要：Tier 2 的語料裡，「國健署新聞」那批（`chunk_index=1` 且有網址
# 的 1,013 筆；2026-09-09 之前這批的 source_name 誤標為「衛福部闢謠網站」，見
# CARE-data 的 migrations/2026_09_09_rename_hpa_source.py）名為闢謠，實際混了
# 大量政策新聞稿——活動開幕、頒獎典禮、國際交流、
# 補助加碼、法規修正草案。這與 CARE-data 對「食藥署公告」的判斷是同一件事
# （該 README：706 篇裡只有 6 篇標題含「謠」字，約兩成是行政公告），差別只在
# 那次在**來源端**擋掉了（`scraper_api.ADMIN_NOISE_KEYWORDS`），這批沒有。
#
# 擋在這裡而不是 ETL 端，是因為這批文章對 RAG 檢索仍然有用（使用者問「政府有沒有
# 補助」時它就是答案），只是不適合當成主動推播的內容。同一批資料，兩種用途，
# 過濾條件本來就該不同。
#
# 量測（2026-09-04，`chunk_index=1` 且有網址的最新 300 篇）：擋下 37 篇，全部
# 來自衛福部，逐篇人工檢視無誤擋；TFC 的 90 篇與食藥署闢謠專區的 5 篇零命中。
#
# 這份清單擋不完，也不打算擋完——漏網的（「翻轉兒童肥胖」這類針對非高齡族群的
# 真衛教）交給第二層的 `KbArticleGrader`。兩層的分工與 `FORBIDDEN_ADVICE_PATTERNS`
# 恰好相反：那裡字串比對是模型之後的保險，這裡字串比對是模型之前的省錢過濾。
POLICY_ANNOUNCEMENT_KEYWORDS: tuple[str, ...] = (
    # 典禮、活動、交流：報導辦了什麼事，不是讀者的身體要注意什麼
    "登場",
    "觀摩",
    "論壇",
    "研討會",
    "座談會",
    "記者會",
    "揭牌",
    "開幕",
    "頒獎",
    "頒發",
    "表揚",
    "殊榮",
    "獲獎",
    "成果展",
    "成果發表",
    "齊聚",
    "攜手",
    "大使",
    "起跑",
    # 抽獎與行銷：目的是參與率，不是知識
    "抽好禮",
    "即享券",
    "抽獎",
    "禮券",
    # 法規與行政進度
    "修正草案",
    "審議通過",
    "公告修正",
    "預告",
    "查緝",
    "精進作為",
    "執行情形",
    "處理原則",
    "納公費",
    "補助調升",
    "再加碼",
    "申請適用",
    # 政績與國際宣傳
    "受國際",
    "赴日內瓦",
    "分享國際",
    "見成效",
    "突破八成",
    "跨國交流",
)

# 明顯不是日期的佔位字串。判定「有沒有可用的日期」時，這些等同於沒有。
_NON_DATE_PLACEHOLDERS: frozenset[str] = frozenset({"不詳", "未提供", "無", "-", "N/A"})

_DATE_PATTERN = re.compile(r"^(\d{2,4})[-/.](\d{1,2})[-/.](\d{1,2})$")

# 民國年與西元年的界線。三位數（含）以下一律當成民國年——西元年不可能是三位數。
#
# **訂正（2026-09-04）**：本行原本寫著「食藥署與衛福部的頁面普遍以民國年呈現」，
# 那是未經查證的推測。實際量測 `health_articles_chunks` 中 `chunk_index=1` 且有
# 網址的 2,422 筆，**民國格式 0 筆**——國健署新聞 1,011 筆、TFC 823 筆、
# 食藥署闢謠專區 587 筆全部是西元 `YYYY-MM-DD`（食藥署的抽取 regex 本身就寫死
# `\d{4}`）。民國年的支援仍然保留：gov.tw 各頁面的日期呈現本來就不一致，上游
# 改版時多認一種格式的成本是零，而少認一種會讓整批文章安靜地消失。
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


def is_policy_announcement(title: str) -> bool:
    """這篇是政策宣傳／活動新聞稿，而不是衛教內容嗎？

    只看標題。內文對這個判斷幫助不大——政策新聞稿的內文同樣充滿「健康」
    「守護」這類字眼，真正的區別在標題講的是「誰辦了什麼」還是「你該注意
    什麼」，而那件事標題就講完了。

    命中的方向刻意保守：誤擋一篇真衛教的代價是使用者當天收到另一篇衛教，
    誤放一篇新聞稿的代價是使用者收到一張與他無關的卡片並學會忽略這張卡
    （見 spec「每日一則與兩層選材」的稀釋論述）。兩者不對稱，所以寧可誤擋。
    """
    return any(keyword in (title or "") for keyword in POLICY_ANNOUNCEMENT_KEYWORDS)


# ── 健康媒體（每日推播的補位來源）────────────────────────────────────
#
# 媒體不像官方來源那樣整站都是衛教：udn 元氣網的「養生」「焦點」裡混著旅遊、
# 家電、兇殺新聞。過濾分兩道，**先分類後標題**：
#
# 1. 分類用**允許清單**而非封鎖清單。站方新增分類時預設不收——與 whitelist.py
#    「沒想到的預設拒絕」同一個原則。代價是新分類裡的好文章會被擋，但那只會讓
#    當天少一篇媒體候選；反過來，封鎖清單漏列一個「性愛」分類，推出去的就是一張
#    不該給長輩看的卡。`tier2_media rejected=` 那行 log 會讓整批被擋的情況看得見。
#
# 2. 標題防線擋允許分類裡的非健康內容。
#
# 量測（2026-09-13，元氣網 2026-08-24～09-13 週 sitemap 364 篇中隨機抽 160 篇，
# 每篇取站方的 article:section）：
#   - 不收的分類 25 篇：醫聲 9、退休力 6、名人 5、性愛 3、活動 1、ESG 1——內容是
#     政策倡議與 podcast、理財、專欄散文、性與伴侶、活動報名、企業 ESG
#   - 允許分類 135 篇：養生 56、焦點 42、醫療 26、癌症 5、慢病好日子 4、失智 2
#   - 允許分類裡逐篇人工檢視，約 25 篇不是健康內容，歸納成下方關鍵字的各組
#   - 下方關鍵字套用後：擋下 26 篇，人工判讀 23 篇確為非健康內容、3 篇屬邊界
#     （內耳前庭發炎的旅平險連載、助聽器配戴建議、面相按穴）；通過的 109 篇裡
#     仍有約 7 篇不是健康內容（牛排上色、出生順序、北歐幸福感…）——這份清單擋
#     不完，也不打算擋完，理由同 POLICY_ANNOUNCEMENT_KEYWORDS
#
# 「新冠肺炎」樣本沒抽到，但 2026-09-14 首頁導覽列上是頂層分類之一，內容是疫情
# 衛教，收。導覽列上其餘沒收的頂層分類：退休力、醫聲、性愛、名人、活動、寵物、ESG。
MEDIA_ALLOWED_CATEGORIES: frozenset[str] = frozenset(
    {"養生", "焦點", "醫療", "癌症", "失智", "慢病好日子", "新冠肺炎"}
)

MEDIA_NOISE_KEYWORDS: tuple[str, ...] = (
    # 旅遊與交通（養生分類裡最大宗的雜訊）
    "航空",
    "機艙",
    "空服員",
    "飯店",
    "郵輪",
    "遊樂園",
    "海外旅遊",  # 旅平險理賠連載，實際主題是保險理賠而非疾病
    "意外險",
    "理賠",
    # 家電、家務、汽車、園藝
    "冰箱",
    "熨斗",
    "家電",
    "汽車",
    "堆肥",
    "花盆",
    # 犯罪與社會事件
    "殺人",
    "棄屍",
    "命案",
    "吸金",
    "重判",
    "遭駭",
    # 迷信與命理。刻意不用「禁忌」：會誤擋「用藥禁忌」這種正是要推的內容
    "鬼月",
    "鬼門",
    "命理",
    "面相",
    "風水",
    "算命",
    "運勢",
    # 企業人物與政策倡議
    "總經理",
    "董事長",
    "執行長",
    "倡議",
    "政策藍圖",
    # 單一產品類別的連載，站方未標示贊助；寧可誤擋（理由同 is_policy_announcement
    # 的不對稱：誤擋一篇只是當天換一篇，誤放一篇業配會讓長輩學會不信這張卡）
    "我的助聽人生",
)
# 刻意不收的字：「保險」會誤擋「全民健康保險」；「股市」會誤擋「股市一震盪就
# 失眠、心悸？」這篇真衛教；「詐騙」會誤擋針對長輩的防詐衛教。


def is_allowed_media_article(category: str | None, title: str) -> bool:
    """這篇媒體文章可以當每日推播的候選嗎？

    三道依序：分類在允許清單內、不是政策／活動稿（沿用 `is_policy_announcement`，
    「誰辦了什麼」不分官方或媒體都不是衛教）、標題不含媒體雜訊字。
    """
    if (category or "").strip() not in MEDIA_ALLOWED_CATEGORIES:
        return False
    if is_policy_announcement(title):
        return False
    return not any(keyword in (title or "") for keyword in MEDIA_NOISE_KEYWORDS)


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
