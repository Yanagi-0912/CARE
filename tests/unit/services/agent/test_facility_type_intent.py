"""
院所類型（醫院／診所／藥局）在 agent 決策層的意圖偵測與跨輪保留。

重點：不能直接用 extract_facility_type_intent() 的結果 —— 它對裸詞「醫院」
會命中（「附近有醫院嗎」→ requested="醫院"），但「醫院」在口語中常泛指
「醫療院所」，若照單全收會把類型過濾套到單純想看病的使用者身上，
將 18,935 家診所全數排除。必須只在「大醫院」「住院」「診所」「藥局」等
明確語彙出現時才觸發，且與科別意圖一樣要能跨輪保留到座標那一則訊息。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.services.agent.utils.nodes import (
    _AMBIGUOUS_FACILITY_TYPE_TERMS,
    _UNAMBIGUOUS_FACILITY_TYPE_TERMS,
    AgentNodes,
    _extract_facility_type_from_history,
    _facility_type_intent,
    _is_nearby_facility_intent,
)
from app.services.medical.department_matcher import extract_department_intent
from app.services.medical.facility_name_index import configure_facility_names
from app.services.medical.facility_type_matcher import all_facility_type_terms

LOCATION_TEXT = "這是我的目前位置：lat=25.033, lng=121.56"

# 專名判定靠的是「這串字是不是登記在案的院所名稱」，答案來自資料庫。
# 這裡注入一份取自真實資料庫的名稱子集（正式營運時由 app/dependencies.py
# 在啟動時載入全部 19,528 筆），讓單元測試不必連線也能驗證同一條判定路徑。
# 之所以要真實名稱而非杜撰名稱：這些院所之所以會被誤判，正是因為它們的名字
# 以高頻用字（家、的、有）結尾，杜撰的名字驗證不到這個特性。
REAL_FACILITY_NAMES = frozenset(
    {
        "皇家診所", "全家診所", "我家診所", "和睦家診所", "宜家診所",
        "安家診所", "怡家診所", "德家診所", "保家診所", "大家診所",
        "家家診所", "林瑞家診所", "優康家診所", "家中醫診所",
        "美的診所", "固的診所", "我的診所",
        "大有診所", "嗎哪診所", "汐止有間診所",
        "一家牙醫診所", "遠東聯想牙醫診所",
        "杏一診所", "康是美藥局", "聯合診所", "小林診所", "屈臣氏藥局",
    }
)


@pytest.fixture(autouse=True)
def facility_name_index():
    """每個測試都在「索引已載入」的狀態下執行，並於結束後清空避免互相污染。"""
    configure_facility_names(REAL_FACILITY_NAMES)
    yield
    configure_facility_names(frozenset())


def _mock_tools(include_rag: bool = True):
    tools = []
    if include_rag:
        for name in ("get_rag_answer", "answer_from_uploaded_document"):
            tool = MagicMock()
            tool.name = name
            tools.append(tool)
    for name in (
        "request_location_quick_reply",
        "find_nearby_hospitals",
        "find_nearby_facilities_by_department",
        "open_official_site",
    ):
        tool = MagicMock()
        tool.name = name
        tools.append(tool)
    return tools


@pytest.fixture
def mock_llm_no_tool_calls():
    llm = MagicMock()
    llm.bind_tools.return_value.ainvoke = AsyncMock(
        return_value=AIMessage(content="腦補")
    )
    return llm


@pytest.fixture
def patched_tools(monkeypatch):
    monkeypatch.setattr(
        "app.services.agent.utils.nodes.get_all_tools",
        lambda include_rag_tool=False: _mock_tools(include_rag=include_rag_tool),
    )


# ---------------------------------------------------------------------------
# _facility_type_intent：嚴格閘門本身
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 明確語彙 → 觸發，且回傳使用者原始說法
        ("附近有大醫院嗎", "大醫院"),
        ("我要找大型醫院", "大型醫院"),
        ("附近有可以住院的地方嗎", "住院"),
        ("附近有診所嗎", "診所"),
        ("找一間小診所", "小診所"),
        ("附近有藥局嗎", "藥局"),
        ("哪裡有藥房", "藥房"),
        ("附近的藥店", "藥店"),
        # 裸詞「醫院」→ 泛稱，不觸發
        ("附近有醫院嗎", None),
        ("我要去醫院", None),
        ("醫院在哪", None),
        # 門診：判斷為與裸詞「醫院」同樣曖昧（醫院也有門診部），不觸發
        ("我要看門診", None),
        ("附近有門診嗎", None),
        # 牙醫／中醫屬於科別維度，本來就解析不出類型
        ("附近有牙醫嗎", None),
        ("哪裡有中醫", None),
        ("", None),
    ],
)
def test_facility_type_intent_gate(text, expected):
    assert _facility_type_intent(text) == expected


def test_nearby_medium_hospital_still_triggers_location_request():
    """4.3：「附近有大醫院嗎」應可觸發要位置的流程（醫院已在 _FACILITY_SEARCH_RE 內）。"""
    assert _is_nearby_facility_intent("附近有大醫院嗎") is True


# ---------------------------------------------------------------------------
# _facility_type_intent：具名院所不得誤觸（修正迴圈第 1 輪 code review）
#
# 白名單詞彙可能只是具名院所全名的一部分：「台大醫院」內含白名單詞彙
# 「大醫院」，「杏一診所」「小林診所」「康是美藥局」內含「診所」「藥局」。
# 這些情況使用者是在查特定院所（依 prompt 規則應走 lookup_medical_facility），
# 不是在表達類型偏好，必須回 None，否則會把類型過濾誤套到查特定院所的對話上。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # review 實跑回報的三個具名院所案例
        "台大醫院在哪",
        "杏一診所在哪裡",
        "康是美藥局在哪",
        # review 特別點名：沒有「在哪」等查詢語尾，仍須擋下
        "杏一診所很近",
        # 品牌詞恰好只有一個字，且不含「在哪」——驗證邊界判定不是只靠 _NAMED_LOOKUP_RE
        "小林診所很推薦",
    ],
)
def test_facility_type_intent_rejects_named_facility(text):
    assert _facility_type_intent(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 明確語彙前面接的是連接詞／量詞（附近有、我要找、可以、還、開的……），
        # 不是品牌名稱，必須維持觸發，不能被上面的具名院所防呆誤傷。
        ("附近有大醫院嗎", "大醫院"),
        ("哪裡有藥局", "藥局"),
        ("我要找大型醫院", "大型醫院"),
        ("附近有可以住院的地方嗎", "住院"),
        ("找一間小診所", "小診所"),
        ("附近現在有開的大醫院嗎", "大醫院"),
    ],
)
def test_facility_type_intent_still_triggers_for_generic_terms(text, expected):
    assert _facility_type_intent(text) == expected


# ---------------------------------------------------------------------------
# _facility_type_intent：修正迴圈第 2 輪 code review 的兩組對照清單
#
# 第 1 輪的邊界檢查（要求整段前綴被連接詞白名單「挖乾淨」）矯枉過正，
# 擋掉了「評價不錯的診所」這類合法泛稱——自然語言修飾語是開放集合，列不完。
# 改用「只看類型詞前面緊鄰的語法標記」後，這兩組清單分別驗證具名院所仍被
# 擋下、且開放式泛稱修飾語不再被誤傷。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "台大醫院在哪",
        "杏一診所在哪裡",
        "康是美藥局在哪",
        "長庚醫院在哪",
        "聯合診所怎麼走",
        "屈臣氏藥局",
        "馬偕紀念醫院在哪",
        "亞東醫院怎麼去",
        "我要去馬偕醫院",
        "附近有醫院嗎",
        "我要去醫院",
        "送去醫院",
        "哪間醫院比較好",
    ],
)
def test_facility_type_intent_must_return_none(text):
    assert _facility_type_intent(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "附近有大醫院嗎",
        "哪裡有藥局",
        "附近有診所嗎",
        "我想去大醫院看病",
        "要住院的話去哪",
        "附近有藥房嗎",
        "評價不錯的診所",
        "24小時營業的藥局",
        "剛開幕的藥局",
        "新開的診所",
        "巷口的診所",
        "住家附近的診所",
        "比較推薦的診所",
        "口碑好的診所",
        "24小時營業的藥局",
        "離我最近的藥局",
    ],
)
def test_facility_type_intent_must_trigger(text):
    assert _facility_type_intent(text) is not None


# ---------------------------------------------------------------------------
# 最終審查 I1／I2：白名單只收 8 個俗稱，導致資料庫正式 type 值的句型靜默失效
#
# extract_facility_type_intent() 採最長優先掃描，「附近有綜合醫院嗎」會解析出
# requested="綜合醫院"（資料庫正式 type 值）。舊的手寫俗稱白名單收不到這些值，
# 整句於是被判定為「沒有類型意圖」，退回不帶 facility_type 的搜尋 —— 也就是
# 退回本 change 原本要修的那個 bug。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("附近有綜合醫院嗎", "綜合醫院"),
        ("附近有專科診所嗎", "專科診所"),
        ("我要找精神科醫院", "精神科醫院"),
        ("附近有牙醫診所嗎", "牙醫診所"),
        ("哪裡有中醫醫院", "中醫醫院"),
        ("附近有西醫診所嗎", "西醫診所"),
    ],
)
def test_facility_type_intent_accepts_canonical_type_values(text, expected):
    assert _facility_type_intent(text) == expected


def test_unambiguous_terms_are_derived_from_matcher_not_hand_listed():
    """
    白名單必須是「matcher 全集扣除歧義詞」，不能是另一份手寫清單——否則
    matcher 新增別名或資料庫新增 type 值時，兩份清單會再次失去同步。
    """
    assert _AMBIGUOUS_FACILITY_TYPE_TERMS <= all_facility_type_terms()
    assert (
        _UNAMBIGUOUS_FACILITY_TYPE_TERMS
        == all_facility_type_terms() - _AMBIGUOUS_FACILITY_TYPE_TERMS
    )
    # 只有裸詞「醫院」與「門診」曖昧，其餘一律視為明確表達。
    assert _AMBIGUOUS_FACILITY_TYPE_TERMS == {"醫院", "門診"}


def test_dental_clinic_maps_to_both_department_and_type():
    """
    specs/location-search「科別詞隱含的類型詞」scenario：使用者要找「附近的
    牙醫診所」時，科別（牙科）與類型（診所）兩個維度必須同時成立。
    這個 scenario 先前完全沒有測試覆蓋。
    """
    text = "附近的牙醫診所"
    department = extract_department_intent(text)
    assert department is not None and department.canonical == "牙科"
    assert _facility_type_intent(text) == "牙醫診所"


# ---------------------------------------------------------------------------
# 最終審查 I3：「家」「的」當單字標記，會誤判大量具名院所
#
# 對真實資料庫 19,106 筆名稱含類型詞的院所實掃，舊規則有 35 家誤觸類型意圖，
# 全部集中在「家」（皇家／全家／我家／和睦家…）與「的」（美的／固的／我的）
# 這兩個高頻命名用字上。以下是實掃結果的完整代表樣本。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # 「家」：品牌用字，前面是內容字
        "皇家診所在哪",
        "全家診所在哪",
        "我家診所在哪",
        "和睦家診所在哪",
        "宜家診所在哪",
        "安家診所在哪",
        "怡家診所在哪",
        "德家診所在哪",
        "保家診所在哪",
        "大家診所在哪",
        "家家診所在哪",
        "林瑞家診所在哪",
        "優康家診所在哪",
        # 「的」：二字品牌名，前面只有一個內容字
        "美的診所在哪",
        "固的診所在哪",
        "我的診所在哪",
        # 其餘實掃到的單字標記誤判
        "大有診所在哪",
        "嗎哪診所在哪",
        "汐止有間診所在哪",
        # 量詞單獨起頭不是中文說法，句首的「家」必然是院所名稱的第一個字
        "家中醫診所在哪",
    ],
)
def test_facility_type_intent_rejects_high_frequency_naming_chars(text):
    assert _facility_type_intent(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 「家」「間」當量詞：前面必有數詞或指示詞，這些仍須觸發
        ("附近有一家診所嗎", "診所"),
        ("這家診所評價如何", "診所"),
        ("哪家藥局比較近", "藥局"),
        ("找兩間診所", "診所"),
        ("幾家大醫院比較好", "大醫院"),
        # 「的」前面有兩個字以上的修飾語
        ("口碑好的診所", "診所"),
        ("離我最近的藥局", "藥局"),
    ],
)
def test_facility_type_intent_keeps_quantifier_and_modifier_forms(text, expected):
    assert _facility_type_intent(text) == expected


def test_extract_facility_type_from_history_rejects_named_facility_from_earlier_turn():
    """
    修正迴圈第 1 輪 code review 回報的跨輪誤判：使用者先查了一間具名診所
    （「杏一診所在哪裡」），幾輪後改問泛稱「附近有醫院嗎」並分享位置，
    不該被誤套 facility_type="診所"。
    """
    messages = [
        HumanMessage(content="杏一診所在哪裡"),
        AIMessage(content="杏一診所地址是..."),
        HumanMessage(content="附近有醫院嗎"),
        AIMessage(content="請分享您的位置"),
        HumanMessage(content=LOCATION_TEXT),
    ]
    assert _extract_facility_type_from_history(messages) is None


# ---------------------------------------------------------------------------
# _extract_facility_type_from_history：跨輪保留
# ---------------------------------------------------------------------------


def test_extract_facility_type_from_history_looks_past_location_message():
    messages = [
        HumanMessage(content="附近有大醫院嗎"),
        AIMessage(content="請分享您的位置"),
        HumanMessage(content=LOCATION_TEXT),
    ]
    assert _extract_facility_type_from_history(messages) == "大醫院"


def test_extract_facility_type_from_history_returns_none_for_bare_hospital():
    messages = [
        HumanMessage(content="附近有醫院嗎"),
        AIMessage(content="請分享您的位置"),
        HumanMessage(content=LOCATION_TEXT),
    ]
    assert _extract_facility_type_from_history(messages) is None


def test_extract_facility_type_from_history_ignores_stale_mentions():
    """太久以前提過的類型需求不該套用到這次搜尋。"""
    messages = [
        HumanMessage(content="附近有大醫院嗎"),
        HumanMessage(content="謝謝"),
        HumanMessage(content="今天天氣如何"),
        HumanMessage(content="血壓多少算高"),
        HumanMessage(content="幫我量一下"),
        HumanMessage(content=LOCATION_TEXT),
    ]
    assert _extract_facility_type_from_history(messages) is None


# ---------------------------------------------------------------------------
# agent_node：4.2 四種組合的注入行為
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shared_location_with_only_department(
    mock_llm_no_tool_calls, patched_tools
):
    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = {
        "messages": [
            HumanMessage(content="附近有腸胃科嗎"),
            AIMessage(content="請分享您的位置"),
            HumanMessage(content=LOCATION_TEXT),
        ],
        "allow_rag": False,
    }

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(state)

    call = res["messages"][0].tool_calls[0]
    assert call["name"] == "find_nearby_facilities_by_department"
    assert call["args"] == {
        "lat": 25.033,
        "lng": 121.56,
        "department": "腸胃科",
    }


@pytest.mark.asyncio
async def test_shared_location_with_only_facility_type(
    mock_llm_no_tool_calls, patched_tools
):
    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = {
        "messages": [
            HumanMessage(content="附近有大醫院嗎"),
            AIMessage(content="請分享您的位置"),
            HumanMessage(content=LOCATION_TEXT),
        ],
        "allow_rag": False,
    }

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(state)

    call = res["messages"][0].tool_calls[0]
    assert call["name"] == "find_nearby_hospitals"
    assert call["args"] == {
        "lat": 25.033,
        "lng": 121.56,
        "facility_type": "大醫院",
    }


@pytest.mark.asyncio
async def test_shared_location_with_department_and_facility_type(
    mock_llm_no_tool_calls, patched_tools
):
    """兩個維度各自獨立，可同時成立（例如「大醫院的腸胃科」）。"""
    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = {
        "messages": [
            HumanMessage(content="附近有大醫院的腸胃科嗎"),
            AIMessage(content="請分享您的位置"),
            HumanMessage(content=LOCATION_TEXT),
        ],
        "allow_rag": False,
    }

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(state)

    call = res["messages"][0].tool_calls[0]
    assert call["name"] == "find_nearby_facilities_by_department"
    assert call["args"] == {
        "lat": 25.033,
        "lng": 121.56,
        "department": "腸胃科",
        "facility_type": "大醫院",
    }


@pytest.mark.asyncio
async def test_shared_location_without_department_or_facility_type(
    mock_llm_no_tool_calls, patched_tools
):
    """向後相容：都沒有需求時，args 不得出現 facility_type 鍵。"""
    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = {
        "messages": [
            HumanMessage(content="附近有醫院嗎"),
            AIMessage(content="請分享您的位置"),
            HumanMessage(content=LOCATION_TEXT),
        ],
        "allow_rag": False,
    }

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(state)

    call = res["messages"][0].tool_calls[0]
    assert call["name"] == "find_nearby_hospitals"
    assert call["args"] == {"lat": 25.033, "lng": 121.56}
    assert "facility_type" not in call["args"]


@pytest.mark.asyncio
async def test_shared_location_ignores_earlier_named_facility_lookup(
    mock_llm_no_tool_calls, patched_tools
):
    """
    修正迴圈第 1 輪 code review 回報的端到端誤判：使用者先查了一間具名診所
    （依 prompt 規則 (c) 應走 lookup_medical_facility，非本測試範圍），
    幾輪後改問泛稱「附近有醫院嗎」並分享位置，不該被誤套
    facility_type="診所"，搜尋範圍不該被不當窄化。
    """
    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = {
        "messages": [
            HumanMessage(content="杏一診所在哪裡"),
            AIMessage(content="杏一診所地址是..."),
            HumanMessage(content="附近有醫院嗎"),
            AIMessage(content="請分享您的位置"),
            HumanMessage(content=LOCATION_TEXT),
        ],
        "allow_rag": False,
    }

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(state)

    call = res["messages"][0].tool_calls[0]
    assert call["name"] == "find_nearby_hospitals"
    assert call["args"] == {"lat": 25.033, "lng": 121.56}
    assert "facility_type" not in call["args"]


@pytest.mark.asyncio
async def test_facility_type_can_combine_with_open_now(
    mock_llm_no_tool_calls, patched_tools
):
    """open_now 的既有行為不得改變，且應能與類型並存。"""
    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = {
        "messages": [
            HumanMessage(content="附近現在有開的大醫院嗎"),
            AIMessage(content="請分享您的位置"),
            HumanMessage(content=LOCATION_TEXT),
        ],
        "allow_rag": False,
    }

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(state)

    call = res["messages"][0].tool_calls[0]
    assert call["name"] == "find_nearby_hospitals"
    assert call["args"] == {
        "lat": 25.033,
        "lng": 121.56,
        "facility_type": "大醫院",
        "open_now": True,
    }


@pytest.mark.asyncio
async def test_stale_facility_type_not_carried_over(
    mock_llm_no_tool_calls, patched_tools
):
    """需求過時（超過回溯上限）不沿用。"""
    nodes = AgentNodes(llm=mock_llm_no_tool_calls, guardrail_service=MagicMock())
    state = {
        "messages": [
            HumanMessage(content="附近有大醫院嗎"),
            HumanMessage(content="謝謝"),
            HumanMessage(content="今天天氣如何"),
            HumanMessage(content="血壓多少算高"),
            HumanMessage(content="幫我量一下"),
            HumanMessage(content=LOCATION_TEXT),
        ],
        "allow_rag": False,
    }

    with patch("app.services.agent.utils.nodes.log_stage"):
        res = await nodes.agent_node(state)

    call = res["messages"][0].tool_calls[0]
    assert call["name"] == "find_nearby_hospitals"
    assert call["args"] == {"lat": 25.033, "lng": 121.56}
    assert "facility_type" not in call["args"]


# --- 先前記為 deferred minor、改用名稱索引後一併修好的情境 ---
#
# 這些在字元規則的三個版本裡都失效過，記在 ledger 的 parked 清單裡。
# 改用「查資料庫確認是否為專名」之後全部自動成立，因此補成回歸測試 ——
# 若哪天有人想回頭加字元規則，這裡會先擋下來。


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # P1：類型詞前綴剛好兩個字。字元規則版本無法分辨這是修飾語還是二字品牌名，
        # 一律判為專名而失效；名稱索引直接查得到答案（這些都不是院所名稱）。
        ("好的診所", "診所"),
        ("新的診所", "診所"),
        ("舊的診所", "診所"),
        ("近的藥局", "藥局"),
        ("小的診所", "診所"),
        ("多家診所", "診所"),
        ("多間診所", "診所"),
        ("另家診所", "診所"),
        # 疑問詞「什麼」「哪些」不在舊的標記清單裡，同樣失效過
        ("有什麼診所", "診所"),
        ("附近有哪些藥局", "藥局"),
        ("這附近有什麼藥局", "藥局"),
    ],
)
def test_previously_deferred_generic_phrases_now_trigger(text, expected):
    assert _facility_type_intent(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        # P4：字元規則版本殘留的兩筆誤判，都是真實登記院所
        "一家牙醫診所在哪",
        "遠東聯想牙醫診所在哪",
    ],
)
def test_previously_residual_named_facilities_now_rejected(text):
    assert _facility_type_intent(text) is None


def test_falls_back_to_generic_when_index_not_loaded():
    """
    索引未載入時（啟動失敗、或單元測試未注入）只剩別名表把關。

    方向必須是漏判而非誤判：「皇家診所」會被當成泛稱而觸發類型過濾，
    退回的是「多套了一個過濾條件」而非給出錯誤結果；而口語簡稱
    「台大醫院」仍由 FACILITY_ALIASES 擋住，不受索引狀態影響。
    """
    configure_facility_names(frozenset())

    assert _facility_type_intent("皇家診所在哪") == "診所"  # 降級：無索引可查
    assert _facility_type_intent("台大醫院在哪") is None  # 別名表仍生效
    assert _facility_type_intent("附近有診所嗎") == "診所"  # 泛稱不受影響
