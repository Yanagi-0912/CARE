import logging
import re
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.core.request_logging import log_stage
from app.core.user_language import normalize_user_language
from app.services.agent.prompt import build_system_prompt
from app.services.agent.utils.state import State
from app.services.medical.department_matcher import extract_department_intent
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

# 修正迴圈第 1 輪原本用「把整段前綴挖乾淨」的封閉連接詞白名單判斷邊界，
# 結果矯枉過正：自然語言修飾語是開放集合（「評價不錯的」「24小時營業的」
# 「剛開幕的」「巷口的」……永遠列不完），要求整段前綴都被白名單消耗殆盡，
# 會連「評價不錯的診所」「24小時營業的藥局」這種明顯泛稱的合法輸入也一併
# 擋掉（見修正迴圈第 2 輪 code review）。
#
# 中文裡「泛稱 vs 專名」有個穩定得多的形態差異：類型詞前面緊鄰的那一兩個字
# 是不是語法標記。「評價不錯的診所」「附近有診所」「一間診所」的「的／有／
# 間」都是修飾語結束、存在句或量詞的標記，本身就宣告後面接的是普通名詞；
# 「杏一診所」「台大醫院」「康是美藥局」的「一」「大」「美」是內容字，直接
# 接在品牌詞後面組成專有名詞，不會有這種語法標記介入。因此只需要檢查緊鄰
# 的字尾是否落在這組標記裡，不需要（也不應該）窮舉修飾語可以有哪些內容。
#
# 標記分成三層，因為「一個字算不算語法標記」與那個字本身的頻率有關（對真實
# 資料庫 19,106 筆含類型詞的院所名稱實掃過，見 final-fix-report）：
#
# 1. 詞組標記（多字）：「附近」「哪裡」等，本身就是完整的語法／語意單位，
#    不可能是院所名稱的尾巴，直接採信。
# 2. 單字標記：「的」「有」「去」等，前面若只剩一個字，那個字必須自己也是
#    標記，否則多半是二字品牌名的頭（「美的診所」「大有診所」「嗎哪診所」）。
# 3. 量詞標記：「家」「間」是純量詞，語法上必定跟在數詞或指示詞後面
#    （一家／幾家／這間／哪間）；「皇家」「全家」「我家」「和睦家」則是品牌
#    用字。因此這兩個字只有在前面緊鄰數詞／指示詞時才算標記。
_FACILITY_TYPE_PHRASE_MARKERS: tuple[str, ...] = (
    "附近", "最近", "周邊", "週邊", "周圍", "鄰近",
    "哪裡", "哪個", "哪有", "可以", "現在", "那種", "這種",
)
_FACILITY_TYPE_SINGLE_MARKERS: tuple[str, ...] = (
    "的", "有", "去", "要", "找", "想", "還", "看", "到", "些", "哪",
)
_FACILITY_TYPE_CLASSIFIER_MARKERS: tuple[str, ...] = ("家", "間")
# 量詞前合法的數詞與指示詞。是封閉的功能詞類（不像修飾語是開放集合），
# 列舉不會有「永遠列不完」的問題。
_FACILITY_TYPE_DETERMINERS = frozenset("一二兩三四五六七八九十幾這那哪某數每各半")


def _is_facility_type_boundary_safe(cleaned: str, index: int) -> bool:
    """
    比對到的白名單詞彙前面緊鄰的字，是不是已知的語法標記。

    是 → 使用者在表達類型偏好（例如「評價不錯的診所」的「的」、
    「附近有大醫院嗎」的「有」）；否 → 前面直接接的是內容字（例如
    「台大醫院」的「台」、「杏一診所」的「一」、「皇家診所」的「皇」），
    視為具名院所全名的一部分，不採信。index==0（比對詞就在句首）沒有前面
    文字可言，直接視為安全。

    刻意只看緊鄰字尾（不要求整段前綴被標記完全消耗）：修飾語內容本身是開放
    集合，只要修飾語結尾接語法標記就足以判斷是泛稱。
    """
    if index == 0:
        return True
    preceding = cleaned[:index]

    if any(preceding.endswith(marker) for marker in _FACILITY_TYPE_PHRASE_MARKERS):
        return True

    if any(preceding.endswith(marker) for marker in _FACILITY_TYPE_SINGLE_MARKERS):
        rest = preceding[:-1]
        # 標記就在句首（「有診所嗎」「的診所」）：前面沒有內容字可疑，採信。
        if not rest:
            return True
        # 前面還有兩個字以上，代表真的有一段修飾語（「評價不錯的」「巷口的」），
        # 而不是二字品牌名（「美的」「大有」）。
        if len(rest) >= 2:
            return True
        # 只剩一個字：那個字必須自己也是語法標記（「哪有藥局」的「哪」、
        # 「想去診所」的「想」），否則就是品牌名的頭（「美的」的「美」）。
        return rest in _FACILITY_TYPE_SINGLE_MARKERS or rest in _FACILITY_TYPE_DETERMINERS

    if any(preceding.endswith(marker) for marker in _FACILITY_TYPE_CLASSIFIER_MARKERS):
        # 量詞不能單獨起頭：「家中醫診所」「間診所」不是中文的說法，句首的
        # 「家」「間」幾乎必然是院所名稱的第一個字（實測「家中醫診所」確有其店）。
        # 因此這裡不比照單字標記給句首豁免，一律要求前面緊鄰數詞或指示詞。
        rest = preceding[:-1]
        return bool(rest) and rest[-1] in _FACILITY_TYPE_DETERMINERS

    return False


def _facility_type_intent(text: str) -> str | None:
    """
    嚴格版的院所類型意圖判定：只有出現明確語彙才回傳使用者的原始說法。

    不能直接回傳 extract_facility_type_intent() 的 match.requested——
    見上方 _AMBIGUOUS_FACILITY_TYPE_TERMS 的說明，裸詞「醫院」會被
    resolve 成 requested="醫院"，必須擋下來。

    白名單詞彙有可能是具名院所全名的一部分（「台大醫院」內含「大醫院」，
    「杏一診所」「康是美藥局」內含「診所」「藥局」），因此比對到之後還要用
    _is_facility_type_boundary_safe() 確認前面那段文字是已知連接詞，
    而不是品牌名稱；比對詞若在文字中出現多次，只要有一次落在安全邊界內
    就採信，全部都疑似具名院所才回 None。
    """
    match = extract_facility_type_intent(text)
    if match is None or match.requested not in _UNAMBIGUOUS_FACILITY_TYPE_TERMS:
        return None

    cleaned = normalize_facility_type_text(text)
    term = match.requested
    index = cleaned.find(term)
    while index != -1:
        if _is_facility_type_boundary_safe(cleaned, index):
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


def _is_nearby_facility_intent(text: str) -> bool:
    if not text or _NAMED_LOOKUP_RE.search(text):
        return False
    if _is_media_extracted_content(text):
        return False
    if _FACILITY_SEARCH_RE.search(text):
        return True
    return _is_nearby_department_intent(text)


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


def format_user_profile_prompt(user_profile: dict | None) -> str:
    if not user_profile:
        return ""

    name = user_profile.get("name") or "未提供"
    gender = user_profile.get("gender") or "未提供"
    age = user_profile.get("age")
    age_str = f"{age} 歲" if age is not None and age > 0 else "未提供"
    height = user_profile.get("height")
    height_str = f"{height} cm" if height and height > 0 else "未提供"
    weight = user_profile.get("weight")
    weight_str = f"{weight} kg" if weight and weight > 0 else "未提供"
    chronic = user_profile.get("chronic_history") or "無"
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
