"""RAG 直通：工具寫好的答案直接回覆，不再問模型組裝一次。"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.i18n.messages import t
from app.services.agent.agent import (
    _insert_before_sources,
    _rag_direct_reply_node,
    _rag_fail_direct_reply_node,
    _route_after_tools,
    _trailing_tool_messages,
)


def _tool(name: str, content) -> ToolMessage:
    return ToolMessage(content=content, name=name, tool_call_id=f"call-{name}")


def _state(messages, allow_rag=True):
    # 預設 allow_rag=True：會走到直通判斷的真實 state，guardrail 必定已放行 RAG。
    return {"messages": messages, "allow_rag": allow_rag}


ANSWER = "高血壓要少鹽 [1]。\n\n參考資料來源：\n[1] 衛福部：https://ex/1"


# ── 路由判斷 ────────────────────────────────────────────────────────


def test_routes_to_direct_when_only_rag_tool_ran():
    state = _state([HumanMessage(content="問題"), _tool("get_rag_answer", ANSWER)])
    assert _route_after_tools(state) == "rag_direct"


def test_multiple_tools_go_back_to_the_model():
    """只有模型能把兩份工具輸出合成一段話。"""
    state = _state(
        [
            HumanMessage(content="問題"),
            _tool("verify_claim", "查核結果"),
            _tool("get_rag_answer", ANSWER),
        ]
    )
    assert _route_after_tools(state) == "agent"


def test_other_tool_alone_goes_back_to_the_model():
    state = _state([HumanMessage(content="問題"), _tool("find_nearby_hospitals", "{}")])
    assert _route_after_tools(state) == "agent"


def test_rag_failure_goes_to_fixed_fail_reply():
    """失敗時不交回模型：2026-09-17 模型把「查無資料」寫成一段沒人問的情緒支持。"""
    from app.services.rag.fail_messages import RagFailCode, rag_fail

    state = _state(
        [HumanMessage(content="問題"), _tool("get_rag_answer", rag_fail(RagFailCode.KB_EMPTY))]
    )
    assert _route_after_tools(state) == "rag_fail_direct"


def _fail_reply(user_text: str, code: str) -> str:
    from app.services.rag.fail_messages import rag_fail

    state = _state([HumanMessage(content=user_text), _tool("get_rag_answer", rag_fail(code, "zh-TW"))])
    (message,) = _rag_fail_direct_reply_node(state)["messages"]
    return message.content


def test_fail_reply_is_the_fixed_text_without_code_or_rag_prefix():
    from app.services.rag.fail_messages import RagFailCode

    reply = _fail_reply("恥笑漸漸光，咱就大聲仔想著煞", RagFailCode.MODEL_REFUSE)

    assert reply == t("rag.fail.MODEL_REFUSE", "zh-TW")
    assert "RAG_ERR" not in reply
    assert not reply.startswith(t("agent.rag_prefix", "zh-TW"))


def test_fail_reply_adds_165_when_user_is_about_to_pay_or_click():
    from app.services.rag.fail_messages import RagFailCode

    reply = _fail_reply("簡訊說健保點數要歸零，叫我點連結填信用卡，要照做嗎", RagFailCode.WEB_EMPTY)

    assert reply.endswith(t("rag.fail.scam_notice"))


def test_timeout_never_gets_scam_notice():
    # 逾時跟使用者問什麼無關，文案是「稍後再問」。
    from app.services.rag.fail_messages import RagFailCode

    reply = _fail_reply("叫我點連結匯款是真的嗎", RagFailCode.TIMEOUT)

    assert reply == t("rag.fail.TIMEOUT", "zh-TW")


def test_previous_turns_tool_messages_do_not_block_passthrough():
    """
    多輪對話的 messages 裡有上一輪的 ToolMessage。若把它們也算進「這一輪用了
    幾個工具」，第二次提問就永遠直通不了。
    """
    state = _state(
        [
            HumanMessage(content="上一輪"),
            _tool("find_nearby_hospitals", "{}"),
            AIMessage(content="上一輪的回覆"),
            HumanMessage(content="這一輪"),
            _tool("get_rag_answer", ANSWER),
        ]
    )
    assert _trailing_tool_messages(state["messages"]) == [state["messages"][-1]]
    assert _route_after_tools(state) == "rag_direct"


# ── 直通節點的輸出 ──────────────────────────────────────────────────


def test_direct_node_returns_tool_text_verbatim():
    out = _rag_direct_reply_node(_state([_tool("get_rag_answer", ANSWER)]))
    text = out["messages"][0].content
    assert isinstance(out["messages"][0], AIMessage)
    assert "高血壓要少鹽 [1]。" in text          # 行內引用刻意保留
    assert "參考資料來源：" in text
    assert "https://ex/1" in text


def test_direct_node_appends_notice_before_sources():
    """
    位置是關鍵：reply.py 組卡片時取的是「來源標題之前」的內容，提醒接在最後
    會讓卡片永遠看不到它。
    """
    from app.i18n.messages import strip_sources_section

    out = _rag_direct_reply_node(_state([_tool("get_rag_answer", ANSWER)]))
    text = out["messages"][0].content
    notice = t("rag.professional_advice_notice")

    assert notice in text
    assert text.index(notice) < text.index("參考資料來源：")
    # 卡片本文（模擬 reply.py 的處理）仍看得到提醒
    assert notice in strip_sources_section(text)


def test_direct_node_handles_answer_without_sources_section():
    from app.i18n.messages import strip_rag_prefix

    out = _rag_direct_reply_node(_state([_tool("get_rag_answer", "沒有來源的答案")]))
    text = out["messages"][0].content
    assert strip_rag_prefix(text).startswith("沒有來源的答案")
    assert t("rag.professional_advice_notice") in text


def test_direct_node_does_not_duplicate_an_existing_notice():
    notice = t("rag.professional_advice_notice")
    out = _rag_direct_reply_node(_state([_tool("get_rag_answer", f"答案\n\n{notice}")]))
    assert out["messages"][0].content.count(notice) == 1


def test_direct_node_flattens_gemini_list_content():
    """工具輸出萬一仍是 list-of-parts，也不能把 Python repr 送給使用者。"""
    content = [{"type": "text", "text": "答案本文", "extras": {"signature": "abc123"}}]
    from app.i18n.messages import strip_rag_prefix

    out = _rag_direct_reply_node(_state([_tool("get_rag_answer", content)]))
    text = out["messages"][0].content
    assert strip_rag_prefix(text).startswith("答案本文")
    assert "signature" not in text and "'type'" not in text


def test_insert_before_sources_is_idempotent_on_plain_text():
    assert _insert_before_sources("純文字", "提醒") == "純文字\n\n提醒"


# ── 前綴 ────────────────────────────────────────────────────────────


def test_direct_node_adds_the_fixed_rag_prefix():
    """
    前綴改由程式附加。它必須是 i18n 的固定字串，`strip_rag_prefix` 才剝得掉——
    模型自己寫的「根據 RAG 資訊，」不是固定字串，會原樣出現在卡片第一行。
    """
    from app.i18n.messages import strip_rag_prefix

    out = _rag_direct_reply_node(_state([_tool("get_rag_answer", ANSWER)]))
    text = out["messages"][0].content
    prefix = t("agent.rag_prefix")

    assert text.startswith(prefix)                      # 純文字路徑看得到
    assert not strip_rag_prefix(text).startswith(prefix)  # 卡片路徑剝得掉


def test_card_body_starts_with_the_answer_not_the_prefix():
    """使用者絕大多數看到的是卡片，第一行必須是答案本身。"""
    from app.i18n.messages import strip_rag_prefix, strip_sources_section

    out = _rag_direct_reply_node(_state([_tool("get_rag_answer", ANSWER)]))
    body = strip_sources_section(strip_rag_prefix(out["messages"][0].content)).strip()

    assert body.startswith("高血壓要少鹽")
    assert "RAG" not in body


def test_direct_node_does_not_duplicate_an_existing_prefix():
    prefix = t("agent.rag_prefix")
    out = _rag_direct_reply_node(_state([_tool("get_rag_answer", f"{prefix}\n答案")]))
    assert out["messages"][0].content.count(prefix) == 1


# ── 直通不能送出錯誤 ────────────────────────────────────────────────


def test_error_status_rag_result_is_not_passed_through():
    """
    ToolNode 出錯時回的是「Error invoking tool …」。2026-09-10 的線上日誌裡，
    模型真的把參數名 query 猜成 question——直通若照送，使用者會看到這段錯誤。
    """
    error = ToolMessage(
        content="Error invoking tool 'get_rag_answer' with kwargs {'question': '法國國歌'} "
        "with error: query: Field required",
        name="get_rag_answer",
        tool_call_id="call-err",
        status="error",
    )
    assert _route_after_tools(_state([HumanMessage(content="法國國歌"), error])) == "agent"


def test_no_direct_reply_when_rag_was_not_offered():
    """本輪沒提供 RAG 時，任何 RAG 結果都只可能是被攔下的呼叫。"""
    state = _state(
        [HumanMessage(content="法國國歌"), _tool("get_rag_answer", ANSWER)],
        allow_rag=False,
    )
    assert _route_after_tools(state) == "agent"
