import logging
import re
import time

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.core.request_logging import log_stage
from app.core.user_language import normalize_user_language
from app.services.agent.prompt import build_system_prompt
from app.services.agent.utils.state import State
from app.tools.registry import get_all_tools

logger = logging.getLogger(__name__)


def _already_ran_rag(messages) -> bool:
    return any(
        isinstance(m, ToolMessage) and m.name == "get_rag_answer" for m in messages
    )


_LOCATION_TOOL_NAMES = frozenset(
    {
        "request_location_quick_reply",
        "find_nearby_hospitals",
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


_FACILITY_SEARCH_RE = re.compile(
    r"醫院|診所|藥局|看醫生|就醫|急診|附近院所|找醫院|找診所|看診|"
    r"hospital|clinic|pharmacy",
    re.IGNORECASE,
)
_NAMED_LOOKUP_RE = re.compile(r"在哪|地址|電話|怎麼去")
_FACILITY_TERM_RE = re.compile(
    r"醫院|診所|藥局|hospital|clinic|pharmacy", re.IGNORECASE
)


def _is_nearby_facility_intent(text: str) -> bool:
    if not text or _NAMED_LOOKUP_RE.search(text):
        return False
    return bool(_FACILITY_SEARCH_RE.search(text))


def _is_named_facility_lookup(text: str) -> bool:
    if not text:
        return False
    return bool(_NAMED_LOOKUP_RE.search(text) and _FACILITY_TERM_RE.search(text))


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
        force_location = False
        user_text = _latest_human_text(state["messages"])

        if (
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
            state.get("allow_rag")
            and "get_rag_answer" in tool_names
            and not tool_calls
            and not _already_ran_rag(state["messages"])
            and not _already_used_location_tools(state["messages"])
            and not _is_nearby_facility_intent(user_text)
            and not _is_named_facility_lookup(user_text)
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
            force_location=force_location or None,
            ms=int((time.perf_counter() - t0) * 1000),
        )

        return {"messages": [response]}
