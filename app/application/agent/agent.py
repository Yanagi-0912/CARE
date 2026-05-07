from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition

from app.application.agent.utils.state import State
from app.application.agent.utils.nodes import AgentNodes
from app.application.agent.prompt import SYSTEM_PROMPT
from app.tools.registry import get_all_tools


class Agent:
    def __init__(self, llm, guardrail_service) -> None:
        self._llm = llm
        self._guardrail_service = guardrail_service
        self._graph = self._build_graph()

    def _build_graph(self):
        """建立並編譯 LangGraph（原子化節點模式）"""
        builder = StateGraph(State)

        # 實例化節點
        nodes = AgentNodes(
            llm=self._llm,
            guardrail_service=self._guardrail_service,
            prompt_instruction=SYSTEM_PROMPT,
        )

        # ToolNode 註冊所有可能的工具（超集），只會執行 LLM 實際呼叫的工具
        all_tools = get_all_tools(include_rag_tool=True)
        tool_executor = ToolNode(all_tools)

        # 加入節點
        builder.add_node("guardrail", nodes.guardrail_node)
        builder.add_node("agent", nodes.agent_node)
        builder.add_node("tools", tool_executor)
        builder.add_node("post_process", nodes.post_process_node)

        # 設定邏輯流程
        builder.add_edge(START, "guardrail")
        builder.add_edge("guardrail", "agent")

        # tools_condition：AI 想用工具 → "tools"；直接回話 → post_process
        builder.add_conditional_edges(
            "agent",
            tools_condition,
            {"tools": "tools", END: "post_process"},
        )

        # 工具執行完 → 回到 agent 讓它根據結果繼續思考
        builder.add_edge("tools", "agent")
        builder.add_edge("post_process", END)

        return builder.compile()

    async def invoke(self, user_input: str) -> dict:
        """對外的主要進入點，回傳格式維持不變。"""
        result = await self._graph.ainvoke(
            {
                "messages": [HumanMessage(content=user_input)],
                "allow_rag": False,
                "call_request_location": False,
            }
        )

        # 從 messages 取得最後的 AI 回覆
        last_msg = result["messages"][-1]
        response = (
            last_msg.content if isinstance(last_msg, AIMessage) else str(last_msg)
        )

        return {
            "response": response,
            "call_request_location": result.get("call_request_location", False),
        }
