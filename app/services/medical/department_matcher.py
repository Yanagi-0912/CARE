"""
把使用者口中的科別說法對應到資料庫真正存在的「部定專科」。

背景：medicalFacilities.departments 只有 44 個 distinct 值，全部是衛福部部定
專科層級（內科、外科、耳鼻喉科…），**沒有任何次專科**。使用者常說的「腸胃科」
「心臟科」「腎臟科」在資料庫裡並不存在，它們都隸屬於「內科」。因此查詢前必須
先做一次俗稱 → 部定專科的映射，並在回覆時誠實告知使用者這層轉換。

刻意不做的事：症狀分診。「胃痛」「胸悶」「頭暈」對應到哪一科屬於醫療判斷，
猜錯的代價是把可能需要急診的人導去一般門診，因此本模組只處理「科別的別名」
（含身體部位的日常說法，如牙齒 → 牙科），不從症狀推科別。

症狀 → 科別的建議另由 app/services/medical/symptom_classification 負責（對照表、
多候選、保底，急迫度判斷擋在 agent 之前）。本模組的紅線不因此放寬，別名表仍不收
症狀詞。
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

# 資料庫實際存在的部定專科（來自 medicalFacilities.departments 的 distinct 值）。
# 僅列出可作為查詢目標的科別；解剖病理科、臨床病理科等民眾不會直接掛號的科別
# 不納入別名表，但仍允許使用者直接輸入正式名稱查詢。
#
# 「家庭醫學科」不在此列：它與「家醫科」在醫療上是同一科，原本在資料庫是兩個
# 獨立值，已由 scripts/normalize_family_medicine_department.py 統一改寫為
# 「家醫科」。使用者仍會說「家庭醫學科」，因此別名表保留該說法。
CANONICAL_DEPARTMENTS: frozenset[str] = frozenset(
    {
        "牙科", "中醫一般科", "不分科", "內科", "兒科", "家醫科", "耳鼻喉科",
        "婦產科", "外科", "眼科", "復健科", "骨科", "精神科", "皮膚科",
        "神經科", "泌尿科", "麻醉科", "放射診斷科", "急診醫學科",
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
    "心臟血管內科": "內科", "心臟血管科": "內科",
    "腎臟科": "內科", "腎臟內科": "內科",
    "胸腔科": "內科", "胸腔內科": "內科",
    "胸腔暨重症加護": "內科", "重症加護科": "內科",
    "結核科": "內科", "結核病科": "內科",
    "新陳代謝科": "內科", "內分泌科": "內科", "新陳代謝及內分泌科": "內科",
    "血液腫瘤科": "內科", "血液科": "內科", "腫瘤科": "內科",
    "風濕免疫科": "內科", "過敏免疫風濕科": "內科", "感染科": "內科",
    "一般內科": "內科",
    # --- 外科次專科 ---
    "一般外科": "外科", "大腸直腸外科": "外科", "心臟外科": "外科",
    "胸腔外科": "外科", "小兒外科": "外科",
    "心臟血管外科": "外科", "心血管外科": "外科",
    "消化外科": "外科", "消化系外科": "外科", "直腸外科": "外科",
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
    "新生兒科": "兒科", "新生兒": "兒科",
    # --- 五官 ---
    "耳科": "耳鼻喉科", "鼻科": "耳鼻喉科", "喉科": "耳鼻喉科",
    "耳鼻喉": "耳鼻喉科", "耳鼻喉頭頸外科": "耳鼻喉科",
    "眼睛": "眼科", "視力": "眼科", "眼": "眼科",
    # --- 牙科 ---
    "牙醫": "牙科", "牙齒": "牙科", "牙": "牙科",
    "矯正科": "齒顎矯正科", "牙齒矯正": "齒顎矯正科", "牙套": "齒顎矯正科",
    "根管治療": "牙髓病科", "抽神經": "牙髓病科",
    "牙周": "牙周病科", "植牙": "口腔顎面外科", "拔智齒": "口腔顎面外科",
    # 官方代碼寫「顏」面、資料庫寫「顎」面。不收的話子字串掃描會退而命中「外科」，
    # 產出一個 is_alias 為 False 的錯誤答案，連別名轉換的告知都不會觸發。
    "口腔顏面外科": "口腔顎面外科", "口腔外科": "口腔顎面外科",
    "顎面外科": "口腔顎面外科", "顏面外科": "口腔顎面外科",
    "兒童牙醫": "兒童牙科", "小兒牙科": "兒童牙科",
    "假牙": "贋復補綴牙科", "牙齒美白": "牙科",
    # --- 骨科／復健 ---
    "骨頭": "骨科", "關節": "骨科", "脊椎": "骨科", "運動傷害": "骨科",
    "脊椎骨科": "骨科",
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
    # 資料庫原本「家醫科」與「家庭醫學科」兩個值並存，已由
    # scripts/normalize_family_medicine_department.py 統一改寫為「家醫科」。
    # 使用者仍會說「家庭醫學科」，故保留為別名。
    "家庭醫學科": "家醫科",
    # 老年醫學次專科由內科或家醫科醫師取得，兩條路都成立。選家醫科是因為
    # 老年醫學門診的本質是整合式初級照護與多重用藥整合，與家醫科職責最貼近，
    # 且診所層級的家醫科密度遠高於內科老年醫學次專科，使用者實際找得到院所。
    "老人醫學科": "家醫科", "老年醫學科": "家醫科", "高齡醫學科": "家醫科",
    "老人科": "家醫科", "老年科": "家醫科",
    "中醫": "中醫一般科", "針灸": "中醫一般科", "推拿": "中醫一般科",
    "國術館": "中醫一般科", "中醫科": "中醫一般科",
    "急診": "急診醫學科", "急診科": "急診醫學科",
    "洗腎": "洗腎科", "透析": "洗腎科", "血液透析": "洗腎科",
    "麻醉": "麻醉科",
    # 疼痛科（FuncType DA）是麻醉科次專科，疼痛門診由麻醉科醫師執業；成大與
    # 台大雲林的掛牌即為「麻醉部／疼痛科」。資料庫沒有「疼痛科」這個值，因此
    # 導向麻醉科。院所登記麻醉科不代表有開疼痛門診，這層落差由 is_alias 觸發的
    # 別名告知承擔——不對映的話使用者只會得到「看不懂」，更沒有幫助。
    # 「疼痛門診」會先被 _INTENT_SUFFIX_RE 剝掉「門診」，不需另收。
    "疼痛科": "麻醉科", "疼痛專科": "麻醉科",
    "職業病": "職業醫學科", "職醫": "職業醫學科",
    "放射科": "放射診斷科", "影像醫學科": "放射診斷科", "X光": "放射診斷科",
    "核醫科": "核子醫學科", "放腫科": "放射腫瘤科",
    "西醫": "西醫一般科",
}

# 從自由文句抽取科別時，把「…專科」「…門診」「看…」等修飾詞剝掉再比對。
_INTENT_SUFFIX_RE = re.compile(r"(專科|門診|醫師|醫生|的醫院|的診所)$")
_INTENT_PREFIX_RE = re.compile(r"^(我要找|我想找|幫我找|想看|要看|去看|找|看)")

# 子字串掃描用的詞彙，長詞優先；一樣長時依字典序，掃描順序才不受 frozenset 的
# 雜湊順序影響。單字別名（如「牙」「眼」）在整句掃描時誤判率太高，只在整句解析時採用。
_SCAN_TERMS: tuple[str, ...] = tuple(
    sorted(
        (term for term in {*CANONICAL_DEPARTMENTS, *DEPARTMENT_ALIASES} if len(term) >= 2),
        key=lambda term: (-len(term), term),
    )
)

# 一句話列舉多個科別時，科別之間只允許隔著這些連接詞，或什麼都不隔。
# 「，」不必收：normalize_department_text 已經把它剝掉，兩個科別會直接相鄰。
_ENUMERATION_GAP_RE = re.compile(r"(?:[、和跟與或及/／]|以及|或是|還是|還有)?")


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

    source: str = "table"
    """解析來源："table" 為別名表命中，"llm" 為表查不到時的 LLM 兜底。
    純供記錄與觀測——用來找出該收編進別名表的高頻俗稱，不影響查詢行為。"""

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


def _department_spans(cleaned: str) -> list[tuple[int, int]]:
    """
    句中每個科別詞的 [start, end)，依出現順序排列。

    長詞先佔位，短詞落在已佔的範圍內就不算：「腸胃內科」裡的「內科」、
    「口腔顏面外科」裡的「外科」都不是另一個科別。
    """
    taken = [False] * len(cleaned)
    spans: list[tuple[int, int]] = []
    for term in _SCAN_TERMS:
        start = cleaned.find(term)
        while start != -1:
            end = start + len(term)
            if not any(taken[start:end]):
                taken[start:end] = [True] * (end - start)
                spans.append((start, end))
            start = cleaned.find(term, start + 1)
    return sorted(spans)


def _is_enumeration_gap(cleaned: str, left: tuple[int, int], right: tuple[int, int]) -> bool:
    """兩個相鄰的科別詞之間是否只隔著連接詞，也就是同一串列舉。"""
    return _ENUMERATION_GAP_RE.fullmatch(cleaned[left[1] : right[0]]) is not None


def extract_department_intents(text: str) -> tuple[DepartmentMatch, ...]:
    """
    從一整句話裡找出使用者要找的科別，例如「附近有沒有腸胃科」→ (內科,)。
    沒有指名科別時回傳空的 tuple。

    一句話可以列舉多科（保底卡按鈕的「搜尋附近的家醫科、內科、不分科」），所以
    回傳一串。但只收與主科別以連接詞相連的那一串：「我在內科看過了，附近有皮膚科
    嗎」要找的是皮膚科，前半句的內科只是轉述，一起搜等於替使用者多加了一個科別。

    主科別取句中最長的科別詞（「腸胃科」不可被較短的詞先吃掉），一樣長時取最前面
    的。舊版一樣長時看 frozenset 的雜湊順序，同一句話每次重啟服務都可能解析成
    不同科別。同一個部定專科只留第一次出現的說法（「腸胃科、心臟科」都是內科）。
    """
    if not text:
        return ()

    cleaned = normalize_department_text(text)
    if not cleaned:
        return ()

    # 先嘗試整句（剝掉常見前後綴）當成純科別解析，命中率最高且最精準。
    stripped = _INTENT_PREFIX_RE.sub("", cleaned)
    stripped = _INTENT_SUFFIX_RE.sub("", stripped)
    direct = resolve_department(stripped)
    if direct is not None:
        return (direct,)

    # 退而求其次：在句中做子字串掃描，再從主科別往左右延伸出同一串列舉。
    spans = _department_spans(cleaned)
    if not spans:
        return ()

    primary = min(
        range(len(spans)), key=lambda i: (spans[i][0] - spans[i][1], spans[i][0])
    )
    first = last = primary
    while first > 0 and _is_enumeration_gap(cleaned, spans[first - 1], spans[first]):
        first -= 1
    while last + 1 < len(spans) and _is_enumeration_gap(
        cleaned, spans[last], spans[last + 1]
    ):
        last += 1

    matches: dict[str, DepartmentMatch] = {}
    for start, end in spans[first : last + 1]:
        match = resolve_department(cleaned[start:end])
        if match is not None:
            matches.setdefault(match.canonical, match)
    return tuple(matches.values())


# departments 為這些值代表「院所沒有申報專科」，不是某一個專科。實務上多為
# 一般西醫診所——巷口那種什麼都看一點的。
UNSPECIFIED_DEPARTMENTS: tuple[str, ...] = ("不分科", "西醫一般科")

# 職責本就等同一般門診的科別。搜尋這幾科時，未申報專科的院所要一併撈出來：
# 使用者站在一間只標「不分科」的診所旁邊搜「附近的內科」卻查無，是把資料的
# 申報粒度當成了臨床事實。反過來，眼科、牙科、婦產科不納入——那些診所做不了
# 那件事，混進去等於把人導去白跑一趟。
GENERAL_PRACTICE_DEPARTMENTS: frozenset[str] = frozenset(
    {"內科", "家醫科", *UNSPECIFIED_DEPARTMENTS}
)


def _departments_regex(values: Sequence[str]) -> dict:
    """
    組出比對 departments 的 MongoDB 條件。

    刻意用 regex 而非精確比對：約 12 筆院所（多為醫學中心）的 departments 是
    「['家醫科、內科、外科、…']」這種整串塞進單一元素的髒資料，精確比對會把
    台大等級的醫院全部漏掉。regex 對陣列欄位會逐元素比對，兩種格式都能命中。
    """
    pattern = "|".join(re.escape(value) for value in values)
    return {"departments": {"$regex": pattern, "$options": "i"}}


def build_department_query(*canonicals: str) -> dict:
    """
    指定科別的查詢條件，可一次給多科，院所有其中任一科即命中。

    只要有一科是通科型，就一併涵蓋未申報專科的院所。
    """
    if not canonicals:
        # 空的 regex 會命中每一家院所，等於靜默退化成不分科別的搜尋。
        raise ValueError("build_department_query 至少需要一個科別")
    values = list(dict.fromkeys(canonicals))
    if any(value in GENERAL_PRACTICE_DEPARTMENTS for value in values):
        values.extend(v for v in UNSPECIFIED_DEPARTMENTS if v not in values)
    return _departments_regex(values)


def build_unspecified_department_query() -> dict:
    """只撈未申報專科的院所。專科搜尋湊不滿時的補充梯次用（見 MedicalService）。"""
    return _departments_regex(UNSPECIFIED_DEPARTMENTS)
