from langchain_core.messages import SystemMessage
from app.application.agent.utils.state import State
from app.tools.registry import get_all_tools


class AgentNodes:
    def __init__(self, llm, guardrail_service, prompt_instruction: str):
        self._llm = llm
        self._guardrail_service = guardrail_service
        self._prompt = prompt_instruction

    async def guardrail_node(self, state: State) -> dict:
        """Guardrail 判斷：從最新的使用者訊息判斷是否允許 RAG。"""
        user_input = state["messages"][-1].content
        allow_rag = await self._guardrail_service.allow_rag_tool(user_input)
        return {"allow_rag": allow_rag}

    async def agent_node(self, state: State) -> dict:
        """LLM 決策節點：根據 allow_rag 動態綁定工具，讓 LLM 決定回話或呼叫工具。"""
        tools = get_all_tools(include_rag_tool=state.get("allow_rag", False))
        llm_with_tools = self._llm.bind_tools(tools)

        # 將系統提示詞注入到對話最前面
        messages = [SystemMessage(content=self._prompt)] + state["messages"]
        response = await llm_with_tools.ainvoke(messages)

        # 直接回傳 AI message，LangGraph 會自動併入 state["messages"]
        return {"messages": [response]}

