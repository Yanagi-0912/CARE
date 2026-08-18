from __future__ import annotations

import logging
import time
from typing import Any, Optional

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from langgraph.prebuilt import ToolNode, tools_condition

from app.core.request_logging import log_stage
from app.i18n.messages import (
    split_at_sources_heading,
    strip_sources_section,
    text_contains_sources_heading,
)
from app.services.agent.utils.nodes import AgentNodes
from app.services.agent.utils.state import State
from app.tools.registry import get_all_tools

logger = logging.getLogger(__name__)

# LangGraph 基本概念：
# - State：流程共用資料（例如 messages、allow_rag）
# - Node：每一步要做的事（函式）
# - Edge：定義下一步走向
# - START / END：流程起點與終點
# 執行時會依邊的定義由 START 流向各節點，最後到 END。

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
                "has_sources": text_contains_sources_heading(text),
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
        )

        all_tools = get_all_tools(include_rag_tool=True)
        tool_executor = ToolNode(all_tools)

        async def tools_node(state: State, config: RunnableConfig) -> dict:

            names = _tool_names_from_state(state)
            t0 = time.perf_counter()
            log_stage(logger, "tools_start", names=names)
            try:
                result = await tool_executor.ainvoke(state, config=config)

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

        logger.info(
            "[Agent] 開始執行，messages=%s, user_input_preview=%s",
            len(messages),
            (user_input or "")[:80],
        )

        result = await self._graph.ainvoke(
            {
                "messages": messages,
                "allow_rag": False,
                "user_profile": user_profile,
            }
        )

        last_msg = result["messages"][-1]
        response = (
            last_msg.content if isinstance(last_msg, AIMessage) else str(last_msg)
        )
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

        # 醫療工具會直接產出要送給 LINE 的內容，避免讓模型重新改寫 Flex JSON。
        medical_tool_names = {
            "find_nearby_hospitals",  # 搜尋附近醫療院所
            "find_nearby_facilities_by_department",  # 搜尋附近特定科別院所
            "lookup_medical_facility",  # 尋找特定醫療院所
            "request_location_quick_reply",  # 分享位置
            "open_official_site",  # 官網／LIFF 入口 Flex
            "verify_claim",  # 查核判定卡 Flex
        }
        used_tool_names: list[str] = []
        for msg in reversed(result.get("messages", [])):
            if (
                isinstance(msg, ToolMessage)
                and getattr(msg, "name", None) in medical_tool_names
            ):
                used_tool_names.append(getattr(msg, "name", ""))
                tool_response = msg.content
                if tool_response is not None:
                    response = (
                        tool_response
                        if isinstance(tool_response, str)
                        else str(tool_response)
                    )
                    logger.info(
                        "[Agent] 已套用醫療工具回覆，tool_name=%s, response_type=%s",
                        getattr(msg, "name", ""),
                        type(tool_response).__name__,
                    )
                break

        if used_tool_names:
            logger.info("[Agent] 醫療工具使用紀錄：%s", used_tool_names)

        # 防禦性後置處理：若呼叫了 get_rag_answer，但 AI 的最終回覆中遺漏了「參考資料來源」，則自動由工具輸出中提取並後補。
        rag_tool_content = None
        for msg in reversed(result.get("messages", [])):
            if getattr(msg, "name", None) == "get_rag_answer":
                rag_tool_content = msg.content
                break

        if rag_tool_content:
            rag_text = (
                rag_tool_content
                if isinstance(rag_tool_content, str)
                else str(rag_tool_content)
            )
            tool_has_sources = text_contains_sources_heading(rag_text)
            response_has_sources = text_contains_sources_heading(response)

            if not tool_has_sources and response_has_sources:
                response = strip_sources_section(response)
                logger.info("[Agent] stripped_fabricated_sources=True")
            elif tool_has_sources and not response_has_sources:
                split = split_at_sources_heading(rag_text)
                if split:
                    heading, sources_body = split
                    response = f"{response.strip()}\n\n{heading}{sources_body.strip()}"

        call_request_location = False
        for msg in result.get("messages", []):
            if getattr(msg, "name", None) == "request_location_quick_reply":
                call_request_location = True
                break

        logger.info(
            "[Agent] 執行完成，response_type=%s, call_request_location=%s",
            type(response).__name__,
            call_request_location,
        )

        return {
            "response": response,
            "call_request_location": call_request_location,
        }
