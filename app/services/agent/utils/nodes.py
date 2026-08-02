import logging
import time

from langchain_core.messages import SystemMessage

from app.core.request_logging import log_stage
from app.core.user_language import normalize_user_language
from app.services.agent.prompt import build_system_prompt
from app.services.agent.utils.state import State
from app.tools.registry import get_all_tools

logger = logging.getLogger(__name__)


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
        called = [tc.get("name") for tc in tool_calls if isinstance(tc, dict)]
        log_stage(
            logger,
            "agent_decide",
            tools=tool_names,
            call=called or None,
            ms=int((time.perf_counter() - t0) * 1000),
        )

        return {"messages": [response]}
