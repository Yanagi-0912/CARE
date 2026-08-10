"""
把使用者口中的科別說法對應到資料庫真正存在的「部定專科」。

背景：medicalFacilities.departments 只有 55 個 distinct 值，全部是衛福部部定
專科層級（內科、外科、耳鼻喉科…），**沒有任何次專科**。使用者常說的「腸胃科」
「心臟科」「腎臟科」在資料庫裡並不存在，它們都隸屬於「內科」。因此查詢前必須
先做一次俗稱 → 部定專科的映射，並在回覆時誠實告知使用者這層轉換。

刻意不做的事：症狀分診。「胃痛」「胸悶」「頭暈」對應到哪一科屬於醫療判斷，
猜錯的代價是把可能需要急診的人導去一般門診，因此本模組只處理「科別的別名」
（含身體部位的日常說法，如牙齒 → 牙科），不從症狀推科別。
"""

import re
from dataclasses import dataclass

# 資料庫實際存在的部定專科（來自 medicalFacilities.departments 的 distinct 值）。
# 僅列出可作為查詢目標的科別；解剖病理科、臨床病理科等民眾不會直接掛號的科別
# 不納入別名表，但仍允許使用者直接輸入正式名稱查詢。
CANONICAL_DEPARTMENTS: frozenset[str] = frozenset(
    {
        "牙科", "中醫一般科", "不分科", "內科", "兒科", "家醫科", "耳鼻喉科",
        "婦產科", "外科", "眼科", "復健科", "骨科", "精神科", "皮膚科",
        "神經科", "泌尿科", "家庭醫學科", "麻醉科", "放射診斷科", "急診醫學科",
        "牙醫一般科", "神經外科", "整形外科", "解剖病理科", "職業醫學科",
        "放射腫瘤科", "核子醫學科", "西醫一般科", "臨床病理科", "口腔顎面外科",
        "家庭牙醫科", "齒顎矯正科", "兒童牙科", "病理科", "牙周病科", "洗腎科",
        "特殊需求者口腔醫學科", "一般精神科", "兒童青少年精神科", "成癮防治科",
        "放射線科", "贋復補綴牙科", "牙髓病科",
    }
)

# 俗稱／次專科 → 部定專科。key 一律為正規化後（去空白、台→臺）的字串。
# 內科次專科佔最大宗：資料庫沒有腸胃科、心臟科、腎臟科…，全部歸「內科」。
DEPARTMENT_ALIASES: dict[str, str] = {
    # --- 內科次專科（資料庫一律只標「內科」）---
    "腸胃科": "內科", "胃腸科": "內科", "腸胃內科": "內科", "胃腸內科": "內科",
    "肝膽腸胃科": "內科", "消化科": "內科", "消化內科": "內科", "肝膽科": "內科",
    "心臟科": "內科", "心臟內科": "內科", "心血管內科": "內科",
    "腎臟科": "內科", "腎臟內科": "內科",
    "胸腔科": "內科", "胸腔內科": "內科",
    "新陳代謝科": "內科", "內分泌科": "內科", "新陳代謝及內分泌科": "內科",
    "血液腫瘤科": "內科", "血液科": "內科", "腫瘤科": "內科",
    "風濕免疫科": "內科", "過敏免疫風濕科": "內科", "感染科": "內科",
    "一般內科": "內科",
    # --- 外科次專科 ---
    "一般外科": "外科", "大腸直腸外科": "外科", "心臟外科": "外科",
    "胸腔外科": "外科", "小兒外科": "外科",
    # 「大腸科」「直腸科」不是正式掛牌名稱，但使用者實際上就是這樣講（實測案例）。
    # 「痔瘡」是本表唯一收錄的病名：它與症狀不同，不存在「猜錯就延誤急診」的分歧
    # ——不論輕重都是掛大腸直腸外科，因此不違反本模組不做症狀分診的原則。
    "大腸科": "外科", "大腸直腸科": "外科", "直腸科": "外科",
    "大腸直腸肛門科": "外科", "肛門科": "外科", "痔瘡": "外科",
    "腦神經外科": "神經外科", "腦外科": "神經外科",
    "美容外科": "整形外科", "醫美": "整形外科", "整外": "整形外科",
    # --- 婦兒 ---
    "婦科": "婦產科", "產科": "婦產科", "婦幼": "婦產科", "生產": "婦產科",
    "小兒科": "兒科", "小兒": "兒科", "兒童科": "兒科",
    # --- 五官 ---
    "耳科": "耳鼻喉科", "鼻科": "耳鼻喉科", "喉科": "耳鼻喉科",
    "耳鼻喉": "耳鼻喉科", "耳鼻喉頭頸外科": "耳鼻喉科",
    "眼睛": "眼科", "視力": "眼科", "眼": "眼科",
    # --- 牙科 ---
    "牙醫": "牙科", "牙齒": "牙科", "牙": "牙科",
    "矯正科": "齒顎矯正科", "牙齒矯正": "齒顎矯正科", "牙套": "齒顎矯正科",
    "根管治療": "牙髓病科", "抽神經": "牙髓病科",
    "牙周": "牙周病科", "植牙": "口腔顎面外科", "拔智齒": "口腔顎面外科",
    "兒童牙醫": "兒童牙科", "小兒牙科": "兒童牙科",
    "假牙": "贋復補綴牙科", "牙齒美白": "牙科",
    # --- 骨科／復健 ---
    "骨頭": "骨科", "關節": "骨科", "脊椎": "骨科", "運動傷害": "骨科",
    "復健": "復健科", "物理治療": "復健科", "職能治療": "復健科",
    # --- 精神／神經 ---
    "身心科": "精神科", "心智科": "精神科", "精神": "精神科",
    "心理科": "精神科", "精神醫學科": "精神科",
    "兒童心智科": "兒童青少年精神科", "青少年精神科": "兒童青少年精神科",
    "戒癮": "成癮防治科", "戒毒": "成癮防治科", "戒酒": "成癮防治科",
    "神經內科": "神經科", "腦神經內科": "神經科", "腦神經科": "神經科",
    # --- 其他 ---
    "泌尿": "泌尿科", "攝護腺": "泌尿科",
    "皮膚": "皮膚科", "皮膚病": "皮膚科",
    "家庭醫學": "家醫科", "家醫": "家醫科", "一般科": "家醫科",
    "中醫": "中醫一般科", "針灸": "中醫一般科", "推拿": "中醫一般科",
    "國術館": "中醫一般科", "中醫科": "中醫一般科",
    "急診": "急診醫學科", "急診科": "急診醫學科",
    "洗腎": "洗腎科", "透析": "洗腎科", "血液透析": "洗腎科",
    "麻醉": "麻醉科",
    "職業病": "職業醫學科", "職醫": "職業醫學科",
    "放射科": "放射診斷科", "影像醫學科": "放射診斷科", "X光": "放射診斷科",
    "核醫科": "核子醫學科", "放腫科": "放射腫瘤科",
    "西醫": "西醫一般科",
}

# 從自由文句抽取科別時，把「…專科」「…門診」「看…」等修飾詞剝掉再比對。
_INTENT_SUFFIX_RE = re.compile(r"(專科|門診|醫師|醫生|的醫院|的診所)$")
_INTENT_PREFIX_RE = re.compile(r"^(我要找|我想找|幫我找|想看|要看|去看|找|看)")


def normalize_department_text(text: str) -> str:
    """去空白、去標點，並統一台→臺（資料庫使用「臺」）。"""
    normalized = re.sub(r"\s+", "", text or "")
    normalized = re.sub(r"[，,。．.？?！!：:；;「」『』()（）\[\]【】]", "", normalized)
    return normalized.replace("台", "臺")


@dataclass(frozen=True)
class DepartmentMatch:
    """一次科別解析的結果。"""

    canonical: str
    """資料庫實際存在的部定專科，例如「內科」。"""

    requested: str
    """使用者原本的說法，例如「腸胃科」。用於回覆時說明轉換。"""

    @property
    def is_alias(self) -> bool:
        """使用者說法與部定專科不同時為 True，回覆需明確說明對應關係。"""
        return self.canonical != self.requested


def resolve_department(text: str) -> DepartmentMatch | None:
    """
    把一段科別說法解析成部定專科。解析不出來回 None（呼叫端須另行處理，
    不要退化成「搜全部科別」，那會讓使用者以為系統真的懂他要的科別）。
    """
    if not text:
        return None

    cleaned = normalize_department_text(text)
    if not cleaned:
        return None

    # 正式名稱直接命中
    if cleaned in CANONICAL_DEPARTMENTS:
        return DepartmentMatch(canonical=cleaned, requested=cleaned)

    # 別名表命中
    alias_target = DEPARTMENT_ALIASES.get(cleaned)
    if alias_target:
        return DepartmentMatch(canonical=alias_target, requested=cleaned)

    # 補「科」再試一次：「腸胃」→「腸胃科」
    if not cleaned.endswith("科"):
        with_suffix = f"{cleaned}科"
        if with_suffix in CANONICAL_DEPARTMENTS:
            return DepartmentMatch(canonical=with_suffix, requested=cleaned)
        alias_target = DEPARTMENT_ALIASES.get(with_suffix)
        if alias_target:
            return DepartmentMatch(canonical=alias_target, requested=cleaned)

    return None


def extract_department_intent(text: str) -> DepartmentMatch | None:
    """
    從一整句話裡找出科別，例如「附近有沒有腸胃科」→ 內科。

    以「最長優先」比對所有已知詞彙，避免「腸胃科」被較短的別名先吃掉。
    """
    if not text:
        return None

    cleaned = normalize_department_text(text)
    if not cleaned:
        return None

    # 先嘗試整句（剝掉常見前後綴）當成純科別解析，命中率最高且最精準。
    stripped = _INTENT_PREFIX_RE.sub("", cleaned)
    stripped = _INTENT_SUFFIX_RE.sub("", stripped)
    direct = resolve_department(stripped)
    if direct is not None:
        return direct

    # 退而求其次：在句中做子字串掃描。長詞優先，否則「腸胃科」會先中「胃」之類的短詞。
    candidates = sorted(
        (*CANONICAL_DEPARTMENTS, *DEPARTMENT_ALIASES),
        key=len,
        reverse=True,
    )
    for candidate in candidates:
        # 單字別名（如「牙」「眼」）在整句掃描時誤判率太高，只在整句解析時採用。
        if len(candidate) < 2:
            continue
        if candidate in cleaned:
            return resolve_department(candidate)

    return None


def build_department_query(canonical: str) -> dict:
    """
    組出比對 departments 的 MongoDB 條件。

    刻意用 regex 而非精確比對：約 12 筆院所（多為醫學中心）的 departments 是
    「['家醫科、內科、外科、…']」這種整串塞進單一元素的髒資料，精確比對會把
    台大等級的醫院全部漏掉。regex 對陣列欄位會逐元素比對，兩種格式都能命中。
    """
    return {"departments": {"$regex": re.escape(canonical), "$options": "i"}}
