from __future__ import annotations

import logging
import time
from typing import Any, Optional

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from app.core.request_logging import log_stage
from app.services.agent.prompt import SYSTEM_PROMPT
from app.services.agent.utils.nodes import AgentNodes
from app.services.agent.utils.state import State
from app.tools.registry import get_all_tools

logger = logging.getLogger(__name__)

TOOL_RESULT_PREVIEW_LEN = 120


def _tool_names_from_state(state: State) -> list[str]:
    messages = state.get("messages") or []
    if not messages:
        return []
    last = messages[-1]
    tool_calls = getattr(last, "tool_calls", None) or []
    names: list[str] = []
    for tc in tool_calls:
        if isinstance(tc, dict):
            name = tc.get("name")
        else:
            name = getattr(tc, "name", None)
        if name:
            names.append(name)
    return names


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
            else:
                parts.append(str(part))
        return "".join(parts)
    return str(content)


def summarize_tool_messages(
    messages: list[Any],
    *,
    preview_len: int = TOOL_RESULT_PREVIEW_LEN,
) -> list[dict[str, Any]]:
    """Build short, monitor-friendly summaries of ToolMessage outputs."""
    summaries: list[dict[str, Any]] = []
    for msg in messages or []:
        if not isinstance(msg, ToolMessage):
            continue
        text = _content_to_text(getattr(msg, "content", ""))
        flat = " ".join(text.split())
        if len(flat) > preview_len:
            preview = flat[:preview_len] + "…"
        else:
            preview = flat
        summaries.append(
            {
                "name": getattr(msg, "name", None) or "tool",
                "preview": preview,
                "has_sources": "參考資料來源" in text,
            }
        )
    return summaries


def _log_tool_result_summaries(messages: list[Any], *, ms: int, names: list[str]) -> None:
    summaries = summarize_tool_messages(messages)
    if not summaries:
        log_stage(logger, "tools_done", names=names, ms=ms)
        return
    for item in summaries:
        log_stage(
            logger,
            "tool_result",
            name=item["name"],
            has_sources=item["has_sources"],
            preview=item["preview"],
        )
    log_stage(logger, "tools_done", names=names, ms=ms)


class Agent:
    def __init__(self, llm, guardrail_service) -> None:
        self._llm = llm
        self._guardrail_service = guardrail_service
        self._graph = self._build_graph()

    def _build_graph(self):
        """建立並編譯 LangGraph（原子化節點模式）"""
        builder = StateGraph(State)

        nodes = AgentNodes(
            llm=self._llm,
            guardrail_service=self._guardrail_service,
            prompt_instruction=SYSTEM_PROMPT,
        )

        all_tools = get_all_tools(include_rag_tool=True)
        tool_executor = ToolNode(all_tools)

        async def tools_node(state: State) -> dict:
            names = _tool_names_from_state(state)
            t0 = time.perf_counter()
            log_stage(logger, "tools_start", names=names)
            try:
                result = await tool_executor.ainvoke(state)
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                result_messages = []
                if isinstance(result, dict):
                    result_messages = result.get("messages") or []
                _log_tool_result_summaries(result_messages, ms=elapsed_ms, names=names)
                return result
            except Exception:
                log_stage(
                    logger,
                    "tools_fail",
                    names=names,
                    ms=int((time.perf_counter() - t0) * 1000),
                )
                raise

        builder.add_node("guardrail", nodes.guardrail_node)
        builder.add_node("agent", nodes.agent_node)
        builder.add_node("tools", tools_node)

        builder.add_edge(START, "guardrail")
        builder.add_edge("guardrail", "agent")
        builder.add_conditional_edges(
            "agent",
            tools_condition,
            {"tools": "tools", END: END},
        )
        builder.add_edge("tools", "agent")

        return builder.compile()

    async def invoke(
        self,
        user_input: str = "",
        messages: Optional[list[AnyMessage]] = None,
        user_profile: Optional[dict] = None,
    ) -> dict:
        """對外的主要進入點，包含使用者的個人對話、歷史與個人健康檔案。"""
        if messages is None:
            messages = [HumanMessage(content=user_input)] if user_input else []
        elif user_input:
            if not messages or messages[-1].content != user_input:
                messages = list(messages) + [HumanMessage(content=user_input)]

        result = await self._graph.ainvoke(
            {
                "messages": messages,
                "allow_rag": False,
                "user_profile": user_profile,
            }
        )

        last_msg = result["messages"][-1]
        response = last_msg.content if isinstance(last_msg, AIMessage) else str(last_msg)
        if isinstance(response, list):
            response = "".join(
                part
                if isinstance(part, str)
                else (part.get("text", "") if isinstance(part, dict) else str(part))
                for part in response
            )
        elif response is None:
            response = ""
        else:
            response = str(response)

        rag_tool_content = None
        for msg in reversed(result.get("messages", [])):
            if getattr(msg, "name", None) == "get_rag_answer":
                rag_tool_content = msg.content
                break

        if rag_tool_content and "參考資料來源：" in rag_tool_content:
            if "參考資料來源：" not in response:
                parts = rag_tool_content.split("參考資料來源：")
                if len(parts) > 1:
                    sources_part = "參考資料來源：" + parts[1]
                    response = f"{response.strip()}\n\n{sources_part.strip()}"

        call_request_location = False
        for msg in result.get("messages", []):
            if getattr(msg, "name", None) == "request_location_quick_reply":
                call_request_location = True
                break

        return {
            "response": response,
            "call_request_location": call_request_location,
        }
