import logging
import re
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.core.request_logging import log_stage
from app.core.user_language import normalize_user_language
from app.services.agent.prompt import build_system_prompt
from app.services.agent.utils.state import State
from app.services.medical.department_matcher import (
    extract_department_intent,
    normalize_department_text,
)
from app.services.medical.facility_name_index import covers_known_facility_name
from app.services.medical.medical_facility_matcher import FACILITY_ALIASES
from app.services.medical.facility_type_matcher import (
    all_facility_type_terms,
    extract_facility_type_intent,
    normalize_facility_type_text,
)
from app.tools.registry import get_all_tools

logger = logging.getLogger(__name__)


def _already_ran_rag(messages) -> bool:
    return any(
        isinstance(m, ToolMessage) and m.name == "get_rag_answer" for m in messages
    )


def _already_ran_upload_document(messages) -> bool:
    return any(
        isinstance(m, ToolMessage) and m.name == "answer_from_uploaded_document"
        for m in messages
    )


_UPLOADED_DOCUMENT_RE = re.compile(
    r"上傳|剛傳|這份(?:PDF|檔|文件|報告)|我傳的|文件裡|PDF裡|報告裡",
    re.IGNORECASE,
)


def _is_uploaded_document_question(text: str) -> bool:
    """使用者追問已上傳文件內容；勿與一般衛教問題混淆。"""
    return bool(text and _UPLOADED_DOCUMENT_RE.search(text))


_LOCATION_TOOL_NAMES = frozenset(
    {
        "request_location_quick_reply",
        "find_nearby_hospitals",
        "find_nearby_facilities_by_department",
        "lookup_medical_facility",
    }
)


def _already_used_location_tools(messages) -> bool:
    return any(
        isinstance(m, ToolMessage) and m.name in _LOCATION_TOOL_NAMES
        for m in messages
    )


def _latest_human_text(messages) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content
    return ""


# LineLocationHandler 會把位置訊息轉成「這是我的目前位置：lat=..., lng=...」
# 這段文字才是 agent 實際看到的輸入。
_SHARED_LOCATION_RE = re.compile(
    r"lat\s*=\s*(-?\d+(?:\.\d+)?)\s*,\s*lng\s*=\s*(-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


def _parse_shared_location(text: str) -> tuple[float, float] | None:
    """從使用者分享位置轉出的文字取出座標，取不到則回 None。"""
    if not text:
        return None
    match = _SHARED_LOCATION_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(1)), float(match.group(2))
    except ValueError:  # pragma: no cover - 正則已限制為數字
        return None


# 使用者提到科別的那一輪通常還沒有座標（「附近有腸胃科嗎」→ 請他分享位置），
# 等座標進來時，最新的一則 HumanMessage 已經是「這是我的目前位置：lat=…」，
# 科別資訊落在更早的訊息裡。只看最後一則會把科別整個弄丟，因此往回掃歷史。
_DEPARTMENT_INTENT_LOOKBACK = 4

# 「使用者有沒有指名某一科」與「那是哪一科」是兩個不同的問題，這裡只回答前者。
#
# 為什麼不能只靠 extract_department_intent：它解析不出來就回 None，於是
# 「指名了某一科，但別名表裡沒有」與「根本沒指名科別」在下游長得一模一樣。
# 前者應該讓 medical_service 用 LLM 兜底、真的兜不出來就誠實說看不懂；後者才該
# 做不分科搜尋。兩者混在一起的後果是科別被靜默丟掉——使用者說「大腸科」，卻拿到
# 一份包含牙科、婦產科的「附近醫療院所」清單，還以為系統聽懂了。
#
# 因此表查不到時改用字面判斷「這句話裡有沒有一個 X 科」，把原始說法原樣往下傳，
# 對應工作全部交給 medical_service 的兩層解析（別名表 → LLM 兜底）。這一層不做
# 任何醫療判斷，也不需要額外的 LLM 呼叫。
_DEPARTMENT_MENTION_RE = re.compile(
    # 排除「科技」「科學」「科系」「科班」「科目」「科普」「科別」「科室」——
    # 這些都是「科」後面還接字的一般詞彙，不是在指某一科。
    r"科(?![技學系班目普別室])"
)

# 找到「科」之後往回取字，遇到這些字就停。
#
# 為什麼是「停止字」而不是「剝掉開頭的語助詞」：後者要求語助詞剛好對齊比對起點，
# 一旦沒對齊就會整段吃進來——實測「請問哪裡有睡眠障礙科」會抽出「裡有睡眠障礙科」、
# 「他大學讀理科」會抽出「他大學讀理科」而逃過非科別詞的過濾。往回走到功能詞為止
# 不受對齊影響，上述兩句分別停在「有」與「讀」，得到「睡眠障礙科」與「理科」。
#
# 刻意不收「家」「學」「大」：家醫科、職業醫學科、大腸科都含這些字，收了會把
# 真正的科別名稱切斷。「哪家」「大學」分別由「哪」「讀／是」擋下。
_MENTION_STOP_CHARS = frozenset(
    "我你他她它們要想去看找有的了嗎呢吧啊嘛喔哦"
    "請問幫附近最周週圍鄰哪沒推薦介紹預約掛號讀"
    "是在這那很和跟與也都就才會能可及對於把被從到往向"
    "科"  # 連續兩個科（「婦產科內科」）不可黏成一個詞
)

# 往回取字的上限。最長的部定專科「兒童青少年精神科」是 8 個字，取到這個長度就夠；
# 再長多半是把整句話吃進來了。
_MENTION_MAX_CHARS = 8

# 字面上是「X科」但不是科別的常見詞。這裡只需要收「別名表查不到、又會被上面
# 正則撈到」的詞——真正的科別（內科、婦產科…）在別名表就命中了，根本走不到這。
_NOT_A_DEPARTMENT = frozenset(
    {
        "理科", "文科", "商科", "工科", "農科", "學科", "術科", "本科",
        "專科", "全科", "分科", "選科", "轉科", "這科", "那科", "哪科",
    }
)


def _looks_like_department_mention(text: str) -> str | None:
    """
    這句話裡有沒有指名某一科？有的話回傳使用者的原始說法，沒有回 None。

    刻意不判斷那是不是真的存在的科別——那是 medical_service 的工作。
    """
    cleaned = normalize_department_text(text)
    if not cleaned:
        return None

    for match in _DEPARTMENT_MENTION_RE.finditer(cleaned):
        end = match.start()  # 「科」本身的位置
        start = end
        while start > 0 and end - start < _MENTION_MAX_CHARS:
            char = cleaned[start - 1]
            if char in _MENTION_STOP_CHARS or not ("一" <= char <= "鿿"):
                break
            start -= 1

        candidate = cleaned[start : end + 1]
        # 只剩「科」一個字代表前面全是功能詞，不是在指名科別。
        if len(candidate) < 2 or candidate in _NOT_A_DEPARTMENT:
            continue
        return candidate

    return None


def _extract_department_from_history(messages) -> str | None:
    """
    由新到舊掃描使用者訊息，找出最近一次提到的科別（回傳使用者的原始說法）。

    只掃最近幾則，避免把很久以前、已經聊完的科別誤套到這次搜尋上。
    """
    scanned = 0
    for message in reversed(messages):
        if not isinstance(message, HumanMessage):
            continue

        text = message.content if isinstance(message.content, str) else ""
        # 位置訊息是系統轉出來的文字，不是使用者在描述需求，跳過但不計入回溯長度。
        if _parse_shared_location(text) is not None:
            continue

        scanned += 1
        if scanned > _DEPARTMENT_INTENT_LOOKBACK:
            return None

        match = extract_department_intent(text)
        if match is not None:
            return match.requested

        # 別名表查不到，但字面上確實指名了某一科 → 原樣往下傳，讓 service 層去對應。
        mention = _looks_like_department_mention(text)
        if mention is not None:
            logger.info(
                "[Agent] 別名表未收錄但字面指名了科別，交由 service 層解析：%r",
                mention,
            )
            return mention

    return None


# 「現在有開的」是使用者主動加上的限定，不能從「附近有診所嗎」推論出來 ——
# 午休時段只有 11.5% 的院所營業，預設篩選會刪掉大量其實稍後就開診的院所。
_OPEN_NOW_RE = re.compile(
    r"現在(?:有|還)?(?:開|營業|看診|門診)|還在(?:看診|營業|開)|"
    r"有開的|開著的|營業中|正在營業|還沒關|來得及|"
    r"open\s*now|still\s*open|open\s*right\s*now",
    re.IGNORECASE,
)


def _is_open_now_intent(text: str) -> bool:
    """使用者是否明確要求只看現在營業中的院所。"""
    if not text or _is_media_extracted_content(text):
        return False
    return bool(_OPEN_NOW_RE.search(text))


def _wants_open_now_from_history(messages) -> bool:
    """
    回溯使用者訊息，判斷是否曾要求「現在有開的」。

    與科別意圖同理：提出需求的那一輪通常還沒有座標，等座標進來時最新訊息已是
    系統轉出的座標文字。
    """
    scanned = 0
    for message in reversed(messages):
        if not isinstance(message, HumanMessage):
            continue

        text = message.content if isinstance(message.content, str) else ""
        if _parse_shared_location(text) is not None:
            continue

        scanned += 1
        if scanned > _DEPARTMENT_INTENT_LOOKBACK:
            return False

        if _is_open_now_intent(text):
            return True

    return False


# 真正有歧義的只有裸詞「醫院」與「門診」兩個：
#   -「醫院」在口語中常泛指「醫療院所」——「我要去醫院」「附近有醫院嗎」多半只是
#     想看病，不代表使用者在意規模／是否要住院。照字面套上醫院類過濾會把
#     18,935 家診所全數排除，反而讓結果更差。
#   -「門診」在別名表裡對應到「診所」，但醫院本身也有門診部，「我要看門診」
#     和裸詞「醫院」一樣是泛稱看病，不足以推論使用者要找診所而非醫院。
# 其餘所有說法（綜合醫院、專科診所、牙醫診所、大醫院、藥師自營…）都已經明確
# 指出規模／型態，沒有這種泛稱問題。
#
# 因此白名單用「facility_type_matcher 認得的全部詞彙 **扣掉** 這兩個歧義詞」
# 表述，而不是正面列舉幾個俗稱。先前正面列舉 8 個俗稱的寫法有個靜默失效的
# 破口：extract_facility_type_intent() 採最長優先掃描，會回傳資料庫的**正式
# type 值**（「附近有綜合醫院嗎」→ requested="綜合醫院"），這些值不在那份手寫
# 清單裡，於是整句被判定為「沒有類型意圖」而退回不帶 facility_type 的搜尋 ——
# 也就是退回本 change 原本要修的那個 bug。改成扣除式之後，別名表或資料庫新增
# 類型值時這裡不必跟著改，兩份清單也就不可能失去同步。
_AMBIGUOUS_FACILITY_TYPE_TERMS = frozenset({"醫院", "門診"})
_UNAMBIGUOUS_FACILITY_TYPE_TERMS = (
    all_facility_type_terms() - _AMBIGUOUS_FACILITY_TYPE_TERMS
)

# 判斷「這個類型詞是專名還是泛稱」的做法演進過三輪字元規則，全部失敗：
#   1. 連接詞白名單（要求整段前綴被消耗殆盡）→ 擋掉「評價不錯的診所」
#   2. 緊鄰語法標記 → 「皇家」「全家」「我家」的「家」被當成量詞標記
#   3. 標記分三層 → 35 家誤判降到 2 家，代價是「好的診所」「多家診所」失效
# 共同的根因是拿字元形態去猜一個事實問題。改用 facility_name_index 直接查
# 資料庫裡的院所名稱，詳見 _looks_like_named_facility。
def _looks_like_named_facility(cleaned: str, start: int, end: int) -> bool:
    """
    `cleaned[start:end]` 這個類型詞，是某家院所專名的最後幾個字嗎？

    先前用三輪字元規則猜過這件事（連接詞白名單 → 緊鄰語法標記 → 標記分三層），
    每輪都在新方向開破口：第一輪擋掉「評價不錯的診所」，第二輪把「皇家診所」
    當成泛稱，第三輪修好 35 家誤判中的 33 家、代價是「好的診所」「多家診所」失效。
    根本問題是拿字元形態去猜一個**事實**——這串字是不是某家院所的名字。
    資料庫裡有答案，不需要猜。

    改成兩道事實查核，任一成立即視為專名：

    1. **登記名稱**：`facility_name_index` 收了全部 19,528 筆院所名稱。
       「皇家診所」「全家診所」「我家診所」「和睦家診所」「美的診所」
       「一家牙醫診所」都確實登記在案；而「好的診所」「多家診所」
       「評價不錯的診所」等泛稱片語沒有一個與院所名稱碰撞。
    2. **口語簡稱**：資料庫存的是正式名稱，使用者說的常是簡稱——
       「台大醫院」在庫裡叫「國立臺灣大學醫學院附設醫院」，查不到。
       這類由既有的 `FACILITY_ALIASES`（臺大／成大／馬偕／榮總／長庚…）補上。

    類型詞位於句首（「診所在哪」）時前面沒有文字可疑，不可能是專名尾巴。
    索引未載入時第 1 道恆為 False，只剩別名表把關——方向是漏判而非誤判，
    退回的是未套類型過濾的現況行為。
    """
    if start == 0:
        return False

    if covers_known_facility_name(cleaned, start, end):
        return True

    return _has_facility_alias_at(cleaned, start)


def _has_facility_alias_at(cleaned: str, start: int) -> bool:
    """
    位置 start 之前是否緊接著一個已知的院所簡稱。

    要處理重疊：「臺大醫院」比對到的類型詞是「大醫院」（start=1），而簡稱
    「臺大」佔的是 [0, 2)，兩者共用「大」這個字。所以判定條件不是
    「前綴以簡稱結尾」，而是「簡稱的結束位置抵達或越過 start」——
    緊鄰（臺大｜醫院）與重疊（臺｜大醫院）兩種情形都涵蓋。
    """
    for alias in FACILITY_ALIASES:
        alias_start = cleaned.find(alias)
        while alias_start != -1:
            alias_end = alias_start + len(alias)
            if alias_start < start <= alias_end:
                return True
            alias_start = cleaned.find(alias, alias_start + 1)
    return False


def _facility_type_intent(text: str) -> str | None:
    """
    嚴格版的院所類型意圖判定：只有出現明確語彙才回傳使用者的原始說法。

    不能直接回傳 extract_facility_type_intent() 的 match.requested——
    見上方 _AMBIGUOUS_FACILITY_TYPE_TERMS 的說明，裸詞「醫院」會被
    resolve 成 requested="醫院"，必須擋下來。

    白名單詞彙有可能是具名院所全名的一部分（「台大醫院」內含「大醫院」，
    「皇家診所」「康是美藥局」內含「診所」「藥局」），因此比對到之後還要用
    _looks_like_named_facility() 查核那是不是某家院所的名字；
    比對詞若在文字中出現多次，只要有一次不是專名的一部分就採信，
    全部都是專名才回 None。
    """
    match = extract_facility_type_intent(text)
    if match is None or match.requested not in _UNAMBIGUOUS_FACILITY_TYPE_TERMS:
        return None

    cleaned = normalize_facility_type_text(text)
    term = match.requested
    index = cleaned.find(term)
    while index != -1:
        if not _looks_like_named_facility(cleaned, index, index + len(term)):
            return term
        index = cleaned.find(term, index + 1)

    return None


def _extract_facility_type_from_history(messages) -> str | None:
    """
    由新到舊掃描使用者訊息，找出最近一次明確表達的院所類型（原始說法）。

    沿用 _extract_department_from_history 的回溯上限與跳過座標訊息的邏輯：
    使用者提出類型需求的那一輪通常還沒有座標，等座標進來時最新的 HumanMessage
    已是系統轉出的座標文字，類型資訊落在更早的訊息裡。
    """
    scanned = 0
    for message in reversed(messages):
        if not isinstance(message, HumanMessage):
            continue

        text = message.content if isinstance(message.content, str) else ""
        if _parse_shared_location(text) is not None:
            continue

        scanned += 1
        if scanned > _DEPARTMENT_INTENT_LOOKBACK:
            return None

        facility_type = _facility_type_intent(text)
        if facility_type is not None:
            return facility_type

    return None


_FACILITY_SEARCH_RE = re.compile(
    r"醫院|診所|藥局|看醫生|就醫|急診|附近院所|找醫院|找診所|看診|"
    r"hospital|clinic|pharmacy",
    re.IGNORECASE,
)
_NAMED_LOOKUP_RE = re.compile(r"在哪|地址|電話|怎麼去")
_FACILITY_TERM_RE = re.compile(
    r"醫院|診所|藥局|hospital|clinic|pharmacy", re.IGNORECASE
)
# LineMediaHandler 會把 OCR／抽字結果包成此前綴再送進 agent。
# 文件全文常含「就醫／診所」等衛教用語，不能當成使用者要找附近院所。
_MEDIA_EXTRACTED_CONTENT_RE = re.compile(
    r"^以下為使用者傳送的(?:image|video|audio|file)媒體內容："
)


def _is_media_extracted_content(text: str) -> bool:
    return bool(text and _MEDIA_EXTRACTED_CONTENT_RE.match(text))


# 「附近有腸胃科嗎」不含醫院／診所等字眼，_FACILITY_SEARCH_RE 抓不到，
# 但它確實是在找附近院所。改以「鄰近詞 + 科別」的組合另行判定。
# 之所以要求同時出現鄰近詞，是為了把「我牙齒痛」這類純症狀敘述排除在外 ——
# 那種情況使用者多半想問衛教，不該直接跳出要位置的按鈕。
_PROXIMITY_RE = re.compile(
    r"附近|最近|周邊|週邊|周圍|鄰近|哪裡有|哪裡可以|哪一家|哪家|推薦|"
    r"nearby|near\s*me|closest|around",
    re.IGNORECASE,
)


def _is_nearby_department_intent(text: str) -> bool:
    """使用者在找「附近的某一科」，例如「附近有腸胃科嗎」。"""
    if not text or _is_media_extracted_content(text):
        return False
    if _NAMED_LOOKUP_RE.search(text):
        return False
    if not _PROXIMITY_RE.search(text):
        return False
    return extract_department_intent(text) is not None


# 「我要看大腸科」既沒有鄰近詞，也沒有醫院／診所字眼，上面兩道判定都抓不到，
# 於是整句掉進強制 RAG —— 使用者想找院所，收到的卻是一段「大腸直腸科醫師在看什麼」
# 的衛教說明。指名科別 + 就診動詞已經足夠明確表達要看這一科，不必再等鄰近詞。
#
# 判定要求動詞緊接在科別前面、科別後面只剩收尾的詞，兩道都是為了不把衛教問句
# 誤判成找院所（那會用一顆要位置的按鈕蓋掉使用者真正想問的問題）：
#   - 只看「句中有沒有就診動詞」→「腸胃科是看什麼的」會因為那個「看」中招。
#   - 只看「動詞是否緊接科別」→「上次看腸胃科醫師說要多喝水，這樣對嗎」這種
#     轉述句仍會中招，因此科別之後只允許「的醫生／醫師／門診」與語助詞收尾。
_DEPARTMENT_VISIT_ACTION_RE = re.compile(r"(?:看|掛號|掛|預約|轉|找)(?:個|一下)?$")
_DEPARTMENT_VISIT_TAIL_RE = re.compile(r"^(?:的?(?:醫生|醫師|門診))?[嗎呢吧啊喔哦耶]*$")


def _is_department_visit_intent(text: str) -> bool:
    """使用者指名科別並表明要就診，例如「我要看大腸科」「幫我掛腸胃科」。"""
    if not text or _is_media_extracted_content(text):
        return False
    if _NAMED_LOOKUP_RE.search(text):
        return False

    # 別名表查不到時沿用 _looks_like_department_mention 的字面判定，
    # 「我要看腹腔鏡科」這類未收錄的說法才不會因為查表落空就掉回 RAG。
    match = extract_department_intent(text)
    term = (
        match.requested if match is not None else _looks_like_department_mention(text)
    )
    if not term:
        return False

    cleaned = normalize_department_text(text)
    index = cleaned.find(term)
    while index != -1:
        before = cleaned[:index]
        after = cleaned[index + len(term) :]
        if _DEPARTMENT_VISIT_ACTION_RE.search(before):
            if _DEPARTMENT_VISIT_TAIL_RE.match(after):
                return True
        index = cleaned.find(term, index + 1)

    return False


def _is_nearby_facility_intent(text: str) -> bool:
    if not text or _NAMED_LOOKUP_RE.search(text):
        return False
    if _is_media_extracted_content(text):
        return False
    if _FACILITY_SEARCH_RE.search(text):
        return True
    return _is_nearby_department_intent(text) or _is_department_visit_intent(text)


def _is_named_facility_lookup(text: str) -> bool:
    if not text or _is_media_extracted_content(text):
        return False
    return bool(_NAMED_LOOKUP_RE.search(text) and _FACILITY_TERM_RE.search(text))


_OFFICIAL_SITE_ACTION_RE = re.compile(
    r"打開|開啟|進入|入口|連結|怎麼開|如何開",
    re.IGNORECASE,
)
_OFFICIAL_SITE_TERM_RE = re.compile(
    r"官網|官方網站|官方入口|網站連結|(?<![a-z])liff(?![a-z])",
    re.IGNORECASE,
)
_OFFICIAL_SITE_DIRECT_RE = re.compile(
    r"^(?:打開官網|打開網站|官網連結|網站連結|官方網站|打開官方網站|"
    r"打開\s*liff|liff\s*入口|liff\s*怎麼開|怎麼開\s*liff|"
    r"官網入口|官方入口)(?:[？?！!。．\s]*)$",
    re.IGNORECASE,
)


def _is_official_site_intent(text: str) -> bool:
    """使用者要開啟官網／LIFF 入口；偏短意圖，避免「官網疫苗資訊」等衛教句誤觸。"""
    if not text or _is_media_extracted_content(text):
        return False
    stripped = text.strip()
    if _OFFICIAL_SITE_DIRECT_RE.match(stripped):
        return True
    if _OFFICIAL_SITE_ACTION_RE.search(stripped) and _OFFICIAL_SITE_TERM_RE.search(
        stripped
    ):
        return True
    return False


def _already_ran_official_site(messages) -> bool:
    return any(
        isinstance(m, ToolMessage) and m.name == "open_official_site" for m in messages
    )


_CHRONIC_CODE_LABELS = {
    "hypertension": "高血壓",
    "diabetes": "糖尿病",
    "hyperlipidemia": "高血脂",
    "heartDisease": "心臟病",
    "kidneyDisease": "腎臟病",
    "asthma": "氣喘",
    "copd": "慢性阻塞性肺病",
    "cancer": "癌症",
}

# 舊資料存過中文，查不到就原樣輸出，與上面同一個規則
_GENDER_LABELS = {"male": "男", "female": "女", "unknown": "未提供"}


def _format_chronic(user_profile: dict) -> str:
    """固定選項的 code 還原成中文，接上使用者自行輸入的病名（後者原文照用）。"""
    names = [
        _CHRONIC_CODE_LABELS.get(code, code)
        for code in user_profile.get("chronic_diseases") or []
    ]
    names += list(user_profile.get("chronic_custom") or [])
    return "、".join(names) or "無"


def format_user_profile_prompt(user_profile: dict | None) -> str:
    if not user_profile:
        return ""

    name = user_profile.get("name") or "未提供"
    gender_code = user_profile.get("gender") or "unknown"
    gender = _GENDER_LABELS.get(gender_code, gender_code)
    age = user_profile.get("age")
    age_str = f"{age} 歲" if age is not None and age > 0 else "未提供"
    height = user_profile.get("height")
    height_str = f"{height} cm" if height and height > 0 else "未提供"
    weight = user_profile.get("weight")
    weight_str = f"{weight} kg" if weight and weight > 0 else "未提供"
    chronic = _format_chronic(user_profile)
    major = user_profile.get("major_illness_history") or "無"
    surgery = user_profile.get("surgery_history") or "無"

    return (
        f"\n\n【對話使用者的個人健康與病史檔案】\n"
        f"- 姓名/稱呼：{name}\n"
        f"- 性別：{gender}\n"
        f"- 年齡：{age_str}\n"
        f"- 身高：{height_str}\n"
        f"- 體重：{weight_str}\n"
        f"- 慢性病史：{chronic}\n"
        f"- 重大疾病史：{major}\n"
        f"- 手術史：{surgery}\n"
        f"請在回答使用者問題時，考慮其個人的健康數據、病史與特殊注意事項，提供適切且具關懷溫度的客製化建議。"
    )


class AgentNodes:
    def __init__(self, llm, guardrail_service):
        self._llm = llm
        self._guardrail_service = guardrail_service

    def _resolve_user_language(self, user_profile: dict | None) -> str:
        if not user_profile:
            return normalize_user_language(None)
        settings = user_profile.get("settings") or {}
        return normalize_user_language(settings.get("language"))

    async def guardrail_node(self, state: State) -> dict:
        """Guardrail 判斷：從最新的使用者訊息判斷是否允許 RAG。"""
        user_input = state["messages"][-1].content
        t0 = time.perf_counter()
        allow_rag = await self._guardrail_service.allow_rag_tool(user_input)
        log_stage(
            logger,
            "guardrail",
            allow_rag=allow_rag,
            ms=int((time.perf_counter() - t0) * 1000),
        )
        return {"allow_rag": allow_rag}

    async def agent_node(self, state: State) -> dict:
        """LLM 決策節點：根據 allow_rag 動態綁定工具，讓 LLM 決定回話或呼叫工具。"""
        tools = get_all_tools(include_rag_tool=state.get("allow_rag", False))
        llm_with_tools = self._llm.bind_tools(tools)
        tool_names = [t.name for t in tools]

        user_profile_text = format_user_profile_prompt(state.get("user_profile"))
        language = self._resolve_user_language(state.get("user_profile"))
        full_prompt = build_system_prompt(language) + user_profile_text
        messages = [SystemMessage(content=full_prompt)] + state["messages"]

        t0 = time.perf_counter()
        response = await llm_with_tools.ainvoke(messages)
        tool_calls = getattr(response, "tool_calls", None) or []
        force_rag = False
        force_upload = False
        force_location = False
        force_nearby = False
        force_official_site = False
        user_text = _latest_human_text(state["messages"])
        shared_location = _parse_shared_location(user_text)

        # 使用者已分享位置（prompt 規則 5(b)）。這裡必須是決定性的：模型若沒有
        # 主動呼叫 find_nearby_hospitals，agent 會回傳空內容，使用者只看到
        # 「抱歉，我無法理解您的問題」；allow_rag 為真時更會把座標文字當成
        # RAG 查詢送出去。順序排在 5(a) 之前 —— 已有座標就不該再要位置。
        if (
            not tool_calls
            and not _already_used_location_tools(state["messages"])
            and shared_location is not None
        ):
            lat, lng = shared_location
            # 使用者稍早若指定過科別（「附近有腸胃科嗎」），座標進來時必須沿用，
            # 否則會退化成搜尋所有科別，回傳一堆牙科、婦產科。
            department = _extract_department_from_history(state["messages"])
            # 類型（大醫院／診所／藥局）與科別是各自獨立的維度，需分開判斷，
            # 兩者可同時帶入同一次工具呼叫（見 _facility_type_intent 的說明）。
            facility_type = _extract_facility_type_from_history(state["messages"])
            if department:
                forced_tool_name = "find_nearby_facilities_by_department"
                forced_args = {"lat": lat, "lng": lng, "department": department}
            else:
                forced_tool_name = "find_nearby_hospitals"
                forced_args = {"lat": lat, "lng": lng}
            if facility_type:
                forced_args["facility_type"] = facility_type
            # 「現在有開的」與科別、類型各自獨立，可單獨成立也可同時成立。
            if _wants_open_now_from_history(state["messages"]):
                forced_args["open_now"] = True
            response = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": forced_tool_name,
                        "args": forced_args,
                        "id": "forced_nearby_1",
                        "type": "tool_call",
                    }
                ],
            )
            tool_calls = response.tool_calls
            called = [forced_tool_name]
            force_nearby = True
        elif (
            not tool_calls
            and not _already_ran_official_site(state["messages"])
            and _is_official_site_intent(user_text)
        ):
            response = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "open_official_site",
                        "args": {},
                        "id": "forced_official_site_1",
                        "type": "tool_call",
                    }
                ],
            )
            tool_calls = response.tool_calls
            called = ["open_official_site"]
            force_official_site = True
        elif (
            not tool_calls
            and not _already_used_location_tools(state["messages"])
            and _is_nearby_facility_intent(user_text)
        ):
            response = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "request_location_quick_reply",
                        "args": {},
                        "id": "forced_location_1",
                        "type": "tool_call",
                    }
                ],
            )
            tool_calls = response.tool_calls
            called = ["request_location_quick_reply"]
            force_location = True
        elif (
            not tool_calls
            and _is_uploaded_document_question(user_text)
            and "answer_from_uploaded_document" in tool_names
            and not _already_ran_upload_document(state["messages"])
        ):
            response = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "answer_from_uploaded_document",
                        "args": {"query": user_text},
                        "id": "forced_upload_1",
                        "type": "tool_call",
                    }
                ],
            )
            tool_calls = response.tool_calls
            called = ["answer_from_uploaded_document"]
            force_upload = True
        elif (
            state.get("allow_rag")
            and "get_rag_answer" in tool_names
            and not tool_calls
            and not _already_ran_rag(state["messages"])
            and not _already_used_location_tools(state["messages"])
            and not _is_nearby_facility_intent(user_text)
            and not _is_named_facility_lookup(user_text)
            and not _is_official_site_intent(user_text)
            and not _is_media_extracted_content(user_text)
            and not _is_uploaded_document_question(user_text)
        ):
            response = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_rag_answer",
                        "args": {"query": user_text},
                        "id": "forced_rag_1",
                        "type": "tool_call",
                    }
                ],
            )
            tool_calls = response.tool_calls
            called = ["get_rag_answer"]
            force_rag = True
        else:
            called = [tc.get("name") for tc in tool_calls if isinstance(tc, dict)]

        log_stage(
            logger,
            "agent_decide",
            tools=tool_names,
            call=called or None,
            force_rag=force_rag or None,
            force_upload=force_upload or None,
            force_location=force_location or None,
            force_nearby=force_nearby or None,
            force_official_site=force_official_site or None,
            ms=int((time.perf_counter() - t0) * 1000),
        )

        return {"messages": [response]}
