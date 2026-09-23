"""查服藥狀況直通：工具組好的文字原樣回給使用者，不再交給模型改寫。

藥名、時間、有沒有確認都是程式從資料庫組的；交回模型重寫，只是多一次把藥名
寫錯的機會，還要多等一次生成。
"""

from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.core.request_context import reset_line_user_id, set_line_user_id
from app.services.agent.agent import (
    Agent,
    _medication_direct_reply_node,
    _route_after_tools,
)
from app.tools import medication_status_tools
from app.tools.medication_status_tools import configure_medication_status_tool

TEXT = "您今天（9/14）的用藥\n\n中 12:30　還沒確認\n• 普拿疼"


def _tool(name, content, status="success"):
    return ToolMessage(content=content, name=name, tool_call_id=f"call-{name}", status=status)


def test_routes_medication_status_straight_to_the_user():
    state = {
        "messages": [HumanMessage(content="我今天要吃什麼藥"), _tool("get_medication_status", TEXT)],
        "allow_rag": True,
    }
    assert _route_after_tools(state) == "medication_direct"


def test_direct_even_when_the_guardrail_did_not_offer_rag():
    """這個工具不隨 RAG 開關提供，guardrail 沒放行 RAG 時也要直通。"""
    state = {
        "messages": [HumanMessage(content="媽媽吃藥了嗎"), _tool("get_medication_status", TEXT)],
        "allow_rag": False,
    }
    assert _route_after_tools(state) == "medication_direct"


def test_medication_status_with_another_tool_goes_back_to_the_model():
    """只有模型能把兩份工具輸出合成一段話。"""
    state = {
        "messages": [
            HumanMessage(content="問題"),
            _tool("get_medication_status", TEXT),
            _tool("get_rag_answer", "答案"),
        ],
        "allow_rag": True,
    }
    assert _route_after_tools(state) == "agent"


def test_errored_call_is_not_sent_to_the_user():
    """參數驗證錯誤（Error invoking tool …）是給模型看的，直通就會原樣送出。"""
    error = _tool("get_medication_status", "Error invoking tool 'get_medication_status'", "error")
    state = {"messages": [HumanMessage(content="問題"), error], "allow_rag": True}
    assert _route_after_tools(state) == "agent"


def test_direct_node_returns_the_tool_text_verbatim():
    out = _medication_direct_reply_node(
        {"messages": [HumanMessage(content="問題"), _tool("get_medication_status", TEXT)]}
    )
    assert isinstance(out["messages"][0], AIMessage)
    assert out["messages"][0].content == TEXT


async def test_agent_replies_with_the_status_text_without_a_second_model_call():
    class Service:
        async def describe(self, asker_id, **kwargs):
            return TEXT

    llm = MagicMock()
    llm.bind_tools.return_value.ainvoke = AsyncMock(
        return_value=AIMessage(
            content="",
            tool_calls=[
                {"name": "get_medication_status", "args": {}, "id": "c1", "type": "tool_call"}
            ],
        )
    )
    guardrail = MagicMock()
    guardrail.allow_rag_tool = AsyncMock(return_value=False)

    previous = medication_status_tools._medication_status_service
    configure_medication_status_tool(Service())
    token = set_line_user_id("U1")
    try:
        result = await Agent(llm, guardrail).invoke(user_input="我今天要吃什麼藥")
    finally:
        reset_line_user_id(token)
        configure_medication_status_tool(previous)

    assert result["response"] == TEXT
    assert llm.bind_tools.return_value.ainvoke.await_count == 1


# ── 清單只是前提時不直通 ────────────────────────────────────────────


def _with_call(user_text, args, content=TEXT):
    """模擬模型發出一次 get_medication_status 呼叫、ToolNode 回了結果的 state。"""
    return {
        "messages": [
            HumanMessage(content=user_text),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_medication_status",
                        "args": args,
                        "id": "call-get_medication_status",
                        "type": "tool_call",
                    }
                ],
            ),
            _tool("get_medication_status", content),
        ],
        "allow_rag": True,
    }


def test_follow_up_question_sends_the_list_back_to_the_model():
    """
    2026-09-23 線上那一則：使用者問「根據我現在吃的藥……這樣是可以的嗎」，
    直通把今天的用藥清單當成答案送出，後半句沒有人回答。清單是問題的前提，
    不是答案。
    """
    state = _with_call(
        "根據我現在吃的藥，我 11 點喝了牛奶、12 點吃藥，等等要吃午餐，這樣可以嗎",
        {"follow_up_question": "我 11 點喝了牛奶、12 點吃藥，等等要吃午餐，這樣可以嗎"},
    )
    assert _route_after_tools(state) == "agent"


def test_pure_lookup_still_goes_straight_through():
    assert _route_after_tools(_with_call("我今天要吃什麼藥", {})) == "medication_direct"


def test_blank_follow_up_question_is_not_a_follow_up():
    """模型把欄位填成空白字串與沒填是同一件事，不能因此多一次生成。"""
    assert (
        _route_after_tools(_with_call("我今天要吃什麼藥", {"follow_up_question": "  "}))
        == "medication_direct"
    )


def test_follow_up_on_another_tools_call_does_not_block_the_shortcut():
    """參數要用 tool_call_id 對應，拿錯呼叫就是拿另一個工具的參數來判斷。"""
    state = {
        "messages": [
            HumanMessage(content="我今天要吃什麼藥"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_family_directory",
                        "args": {"follow_up_question": "別的問題"},
                        "id": "call-other",
                        "type": "tool_call",
                    },
                    {
                        "name": "get_medication_status",
                        "args": {},
                        "id": "call-get_medication_status",
                        "type": "tool_call",
                    },
                ],
            ),
            _tool("get_medication_status", TEXT),
        ],
        "allow_rag": True,
    }
    assert _route_after_tools(state) == "medication_direct"


# ── 兩個新工具的直通 ────────────────────────────────────────────────


def test_medication_question_goes_straight_through():
    """答案已經過 RAG 生成、時間那段是程式算的，交回模型只是重寫成品。"""
    state = {
        "messages": [
            HumanMessage(content="我的藥可以配牛奶嗎"),
            _tool("ask_about_my_medications", "答案\n\n（以下是 CARE 裡登記的資料：…）"),
        ],
        "allow_rag": True,
    }
    assert _route_after_tools(state) == "medication_question_direct"


def test_medication_question_direct_node_adds_the_medical_notice():
    from app.i18n.messages import t
    from app.services.agent.agent import _medication_question_direct_reply_node

    answer = "答案本文\n\n參考資料來源：\n[1] 衛福部：https://ex/1"
    out = _medication_question_direct_reply_node(
        {"messages": [HumanMessage(content="問題"), _tool("ask_about_my_medications", answer)]}
    )
    text = out["messages"][0].content
    notice = t("rag.professional_advice_notice")
    assert notice in text
    # 位置與 rag_direct 相同：卡片取的是來源標題之前的內容。
    assert text.index(notice) < text.index("參考資料來源：")


def test_medication_report_goes_straight_through():
    state = {
        "messages": [
            HumanMessage(content="我 12 點吃藥了"),
            _tool("record_medication_taken", "好，已記錄您在 12:00 服用早 08:00 這一頓："),
        ],
        "allow_rag": False,
    }
    assert _route_after_tools(state) == "medication_report_direct"


def test_medication_report_direct_node_returns_the_text_verbatim():
    from app.services.agent.agent import _medication_report_direct_reply_node

    text = "好，已記錄您在 12:00 服用早 08:00 這一頓：\n✓ 普拿疼"
    out = _medication_report_direct_reply_node(
        {"messages": [HumanMessage(content="問題"), _tool("record_medication_taken", text)]}
    )
    assert out["messages"][0].content == text


async def test_follow_up_question_costs_one_more_model_call_and_answers_it():
    """
    端到端：模型只挑了 get_medication_status，但填了 `follow_up_question`。
    直通被擋下來，清單回到模型手上，第二次呼叫才寫出使用者真正問的那一句。
    """
    class Service:
        async def describe(self, asker_id, **kwargs):
            return TEXT

    llm = MagicMock()
    llm.bind_tools.return_value.ainvoke = AsyncMock(
        side_effect=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "get_medication_status",
                        "args": {"follow_up_question": "這樣跟牛奶一起吃可以嗎"},
                        "id": "c1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="您今天的藥如上。牛奶不影響這幾種藥的吸收。"),
        ]
    )
    guardrail = MagicMock()
    guardrail.allow_rag_tool = AsyncMock(return_value=False)

    previous = medication_status_tools._medication_status_service
    configure_medication_status_tool(Service())
    token = set_line_user_id("U1")
    try:
        result = await Agent(llm, guardrail).invoke(
            user_input="我今天要吃什麼藥？這樣跟牛奶一起吃可以嗎"
        )
    finally:
        reset_line_user_id(token)
        configure_medication_status_tool(previous)

    assert "牛奶" in result["response"]
    assert llm.bind_tools.return_value.ainvoke.await_count == 2


async def test_medication_question_answer_becomes_a_card_like_a_rag_answer():
    """答案是 RAG 生成的，就該有來源按鈕、也該剝掉前綴——跟 RAG 答案同一套。"""
    from app.tools import medication_question_tools
    from app.tools.medication_question_tools import configure_medication_question_tool

    answer = "牛奶不影響吸收。\n\n參考資料來源：\n[1] 食藥署：https://ex/1"

    class Service:
        async def answer(self, asker_id, question, **kwargs):
            return answer

    llm = MagicMock()
    llm.bind_tools.return_value.ainvoke = AsyncMock(
        return_value=AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "ask_about_my_medications",
                    "args": {"question": "我的藥可以配牛奶嗎"},
                    "id": "c1",
                    "type": "tool_call",
                }
            ],
        )
    )
    guardrail = MagicMock()
    guardrail.allow_rag_tool = AsyncMock(return_value=True)

    previous = medication_question_tools._medication_question_service
    configure_medication_question_tool(Service())
    token = set_line_user_id("U1")
    try:
        result = await Agent(llm, guardrail).invoke(user_input="我的藥可以配牛奶嗎")
    finally:
        reset_line_user_id(token)
        configure_medication_question_tool(previous)

    assert result["answer_kind"] == "rag"
    assert llm.bind_tools.return_value.ainvoke.await_count == 1


async def test_medication_question_without_sources_is_not_made_into_a_card():
    """RAG 沒答出來時回的是登記資料加「請問藥師」，沒有來源可掛。"""
    from app.tools import medication_question_tools
    from app.tools.medication_question_tools import configure_medication_question_tool

    class Service:
        async def answer(self, asker_id, question, **kwargs):
            return "這個問題我查不到可靠的資料，請直接問藥師或醫師。\n\n以下是 CARE 裡登記的資料：\n…"

    llm = MagicMock()
    llm.bind_tools.return_value.ainvoke = AsyncMock(
        return_value=AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "ask_about_my_medications",
                    "args": {"question": "我的藥可以配牛奶嗎"},
                    "id": "c1",
                    "type": "tool_call",
                }
            ],
        )
    )
    guardrail = MagicMock()
    guardrail.allow_rag_tool = AsyncMock(return_value=True)

    previous = medication_question_tools._medication_question_service
    configure_medication_question_tool(Service())
    token = set_line_user_id("U1")
    try:
        result = await Agent(llm, guardrail).invoke(user_input="我的藥可以配牛奶嗎")
    finally:
        reset_line_user_id(token)
        configure_medication_question_tool(previous)

    assert result["answer_kind"] is None
