"""
把使用者口中的院所類型說法對應到資料庫真正存在的 `type` 值。

背景：medicalFacilities.type 有 17 個 distinct 值（如「醫院」「綜合醫院」
「專科診所」「藥師自營」…），使用者通常只會說「醫院」「診所」「藥局」這種
大分類，不會知道資料庫把「醫院」細分成「醫院／綜合醫院／精神科醫院／中醫醫院」
四種。因此查詢前必須先把使用者說法映射到分類，再展開成該分類底下所有實際
`type` 值。

刻意不做的事：「牙醫」「中醫」不納入本模組的別名表。這兩個詞已經是
`department_matcher.py` 的別名（→ 牙科、中醫一般科），若同時作為類型分類，
同一個詞會映射到「科別」與「類型」兩個維度，查詢時以 $and 疊加反而縮小結果集
——實測 departments 含「中醫一般科」有 3,517 筆，但中醫類型的院所只有 3,496 筆，
差額是綜合醫院掛的中醫部，使用者問「附近有中醫嗎」時把這些排除掉並非本意。
因此本模組只在「醫院／診所／藥局」這個分類維度上做事，牙醫、中醫交給科別維度。

「病理中心」雖是資料庫 17 個 type 值之一，但民眾不會主動前往（純檢驗機構），
故只列在 CANONICAL_FACILITY_TYPES 供完整性核對，不歸入任何分類、也無法被解析出。
"""

import re
from dataclasses import dataclass

# 資料庫實際存在的 17 個 `type` 值（醫療院所開業資料 distinct 值，逐字採用）。
CANONICAL_FACILITY_TYPES: frozenset[str] = frozenset(
    {
        "專科診所", "一般診所(醫務室)", "牙醫一般診所", "中醫一般診所",
        "牙醫診所", "西醫診所", "中醫診所", "中醫專科診所", "牙醫專科診所",
        "西醫診所(醫務室)",
        "醫院", "綜合醫院", "精神科醫院", "中醫醫院",
        "藥師自營", "藥劑生自營",
        "病理中心",
    }
)

# 分類 → 該分類底下所有 type 值，依資料筆數由多到少排序（僅供閱讀方便，
# 查詢結果不受順序影響）。刻意用 tuple 而非 set：build_facility_type_query()
# 組出的 $in 陣列順序需要穩定、可預期，方便寫測試與除錯時比對。
FACILITY_TYPE_CATEGORIES: dict[str, tuple[str, ...]] = {
    "診所": (
        "專科診所", "一般診所(醫務室)", "牙醫一般診所", "中醫一般診所",
        "牙醫診所", "西醫診所", "中醫診所", "中醫專科診所", "牙醫專科診所",
        "西醫診所(醫務室)",
    ),
    "醫院": (
        "醫院", "綜合醫院", "精神科醫院", "中醫醫院",
    ),
    "藥局": (
        "藥師自營", "藥劑生自營",
    ),
}

# type 值 → 所屬分類的反向查表，供 resolve_facility_type() 直接輸入正式
# type 值（如「專科診所」）時查出分類。刻意不含「病理中心」：它不在
# FACILITY_TYPE_CATEGORIES 裡，代表本來就不該被解析成任何使用者可選類型。
_TYPE_VALUE_TO_CATEGORY: dict[str, str] = {
    value: category
    for category, values in FACILITY_TYPE_CATEGORIES.items()
    for value in values
}

# 俗稱 → 分類。key 一律為正規化後（去空白、台→臺）的字串。
# 刻意只收語意明確指向「醫院規模／要住院」的詞，不收裸詞「醫院」的近義詞
# （如「醫療院所」），因為使用者說「醫院」常常泛指所有院所，若把這種泛稱詞
# 也塞進別名表會誤導本來語意就模糊的查詢；裸詞「醫院」本身已是
# FACILITY_TYPE_CATEGORIES 的 key，仍可直接解析出來，要不要在對話中套用
# 交給後續的意圖偵測決定，不是本模組的責任。
FACILITY_TYPE_ALIASES: dict[str, str] = {
    "大醫院": "醫院", "大型醫院": "醫院", "住院": "醫院",
    "小診所": "診所", "門診": "診所",
    "藥房": "藥局", "藥店": "藥局",
}

# 從自由文句抽取類型時，把常見的「我要找」「附近有」等前綴、「嗎」「呢」等
# 語尾助詞剝掉再比對整句。注意：與 department_matcher 不同，這裡不能剝掉
# 「的醫院」「的診所」這類後綴——那正是本模組要找的目標詞，剝掉會找不到。
_INTENT_PREFIX_RE = re.compile(
    r"^(我要找|我想找|幫我找|我要去|我想去|想去|要去|哪裡有|哪有|有沒有|附近有|去|找)"
)
_INTENT_SUFFIX_RE = re.compile(r"(嗎|呢|在哪裡|在哪)$")


def all_facility_type_terms() -> frozenset[str]:
    """
    回傳 resolve_facility_type() 認得的全部詞彙：分類名（醫院／診所／藥局）、
    別名（大醫院、藥房…）與資料庫正式 type 值（綜合醫院、牙醫診所、藥師自營…）。

    存在的理由：呼叫端（agent 的類型意圖閘門）需要判斷「使用者這句話裡的類型詞
    算不算明確」。若呼叫端自己列一份詞彙清單，就會多出一份必須與本模組手動同步的
    封閉清單——別名表或資料庫新增類型值時，呼叫端漏改就會靜默失效（正式 type 值
    解析得出來、卻在閘門被擋掉）。改成由本模組公開全集，呼叫端只要表述
    「全集扣除哪幾個歧義詞」，兩邊就不可能失去同步。
    """
    return frozenset(
        {*FACILITY_TYPE_CATEGORIES, *FACILITY_TYPE_ALIASES, *_TYPE_VALUE_TO_CATEGORY}
    )


def normalize_facility_type_text(text: str) -> str:
    """去空白、去標點，並統一台→臺（資料庫使用「臺」）。"""
    normalized = re.sub(r"\s+", "", text or "")
    normalized = re.sub(r"[，,。．.？?！!：:；;「」『』()（）\[\]【】]", "", normalized)
    return normalized.replace("台", "臺")


@dataclass(frozen=True)
class FacilityTypeMatch:
    """一次院所類型解析的結果。"""

    category: str
    """使用者查詢意圖所屬的分類：「醫院」「診所」或「藥局」。"""

    requested: str
    """使用者原本的說法，例如「大醫院」。用於回覆時說明轉換。"""

    source: str = "table"
    """解析來源："table" 為別名表命中，"llm" 為表查不到時的 LLM 兜底。
    純供記錄與觀測，不影響查詢行為。"""

    @property
    def is_alias(self) -> bool:
        """使用者說法與分類名稱本身不同時為 True，回覆需明確說明對應關係。"""
        return self.requested != self.category


def resolve_facility_type(text: str) -> FacilityTypeMatch | None:
    """
    把一段院所類型說法解析成分類（醫院／診所／藥局）。解析不出來回 None
    （呼叫端須另行處理，不要退化成「搜全部院所」，那會讓使用者以為系統真的
    懂他要的類型）。

    「牙醫」「中醫」必定回 None，見模組 docstring 的說明。
    """
    if not text:
        return None

    cleaned = normalize_facility_type_text(text)
    if not cleaned:
        return None

    # 分類名稱本身直接命中（「醫院」「診所」「藥局」）
    if cleaned in FACILITY_TYPE_CATEGORIES:
        return FacilityTypeMatch(category=cleaned, requested=cleaned)

    # 別名表命中
    alias_target = FACILITY_TYPE_ALIASES.get(cleaned)
    if alias_target:
        return FacilityTypeMatch(category=alias_target, requested=cleaned)

    # 使用者直接輸入資料庫正式 type 值（如「專科診所」「藥師自營」）
    category = _TYPE_VALUE_TO_CATEGORY.get(cleaned)
    if category:
        return FacilityTypeMatch(category=category, requested=cleaned)

    return None


def extract_facility_type_intent(text: str) -> FacilityTypeMatch | None:
    """
    從一整句話裡找出院所類型，例如「附近有沒有醫院」→ 醫院。

    以「最長優先」比對所有已知詞彙，避免「醫院」被「大型醫院」這種更精確的
    詞搶先命中前就先比對到短詞，導致 requested 遺失使用者原本更明確的說法。
    """
    if not text:
        return None

    cleaned = normalize_facility_type_text(text)
    if not cleaned:
        return None

    # 先嘗試整句（剝掉常見前後綴）當成純類型解析，命中率最高且最精準。
    stripped = _INTENT_PREFIX_RE.sub("", cleaned)
    stripped = _INTENT_SUFFIX_RE.sub("", stripped)
    direct = resolve_facility_type(stripped)
    if direct is not None:
        return direct

    # 退而求其次：在句中做子字串掃描。長詞優先，否則「醫院」會比「大型醫院」
    # 先命中，讓使用者更明確的說法被蓋掉。
    candidates = sorted(all_facility_type_terms(), key=len, reverse=True)
    for candidate in candidates:
        if candidate in cleaned:
            return resolve_facility_type(candidate)

    return None


def build_facility_type_query(category: str) -> dict:
    """
    組出比對 type 欄位的 MongoDB 條件。

    刻意用 $in 精確比對而非 regex：type 欄位乾淨（不像 departments 有整串
    塞進單一元素的髒資料），但值互為子字串——「醫院」是「綜合醫院」
    「中醫醫院」「精神科醫院」的子字串，regex 無法只比對「醫院」本身而不
    誤中其他三種，$in 才能精確列出分類底下所有實際值。
    """
    return {"type": {"$in": list(FACILITY_TYPE_CATEGORIES[category])}}
