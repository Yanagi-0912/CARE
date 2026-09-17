"""RAG 分流資料集的標籤規則，以及標註時 agent 看到的工具清單。

標籤是正式 agent 的決定，所以兩件事要釘住：只有「單獨呼叫 get_rag_answer」才算
走 RAG；標註時提供的工具要跟正式環境一樣——第一次標註漏了 verify_claim，查謠言的
訊息全被標成 RAG。
"""

from langchain_core.messages import AIMessage

import scripts.build_rag_route_dataset as build
from app.tools import claim_tools


def _message(*calls):
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": call_id, "type": "tool_call"}
            for name, args, call_id in calls
        ],
    )


def test_lone_rag_call_is_positive_and_keeps_query():
    route = build.agent_route(_message(("get_rag_answer", {"query": "痛風 飲食"}, "c1")))

    assert route == {
        "calls": ["get_rag_answer"],
        "label": 1,
        "rag_query": "痛風 飲食",
        "forced_rag": False,
    }


def test_forced_rag_is_positive_and_flagged():
    route = build.agent_route(_message(("get_rag_answer", {"query": "原句"}, "forced_rag_1")))

    assert route["label"] == 1
    assert route["forced_rag"] is True


def test_other_tool_or_multiple_tools_are_negative():
    assert build.agent_route(_message(("verify_claim", {"query": "q"}, "c1")))["label"] == 0
    assert (
        build.agent_route(
            _message(("get_rag_answer", {"query": "q"}, "c1"), ("verify_claim", {"query": "q"}, "c2"))
        )["label"]
        == 0
    )
    assert build.agent_route(AIMessage(content="你好"))["label"] == 0


def test_labeling_offers_claim_tool_like_production(monkeypatch):
    monkeypatch.setattr(build.settings, "CLAIM_VERIFICATION_ENABLED", True)
    monkeypatch.setattr(claim_tools, "_claim_verification_service", None)

    offered = build.offer_production_tools()

    assert "verify_claim" in offered
    assert "get_rag_answer" in offered
