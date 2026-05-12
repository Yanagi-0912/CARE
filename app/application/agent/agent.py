from langchain_core.messages import HumanMessage, AIMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition

from app.application.agent.utils.state import State
from app.application.agent.utils.nodes import AgentNodes
from app.application.agent.prompt import SYSTEM_PROMPT
from app.tools.registry import get_all_tools

# LangGraph 基本概念：
# - State：流程共用資料（例如 messages、allow_rag）
# - Node：每一步要做的事（函式）
# - Edge：定義下一步走向
# - START / END：流程起點與終點
# 執行時會依邊的定義由 START 流向各節點，最後到 END。


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

        # 設定邏輯流程
        # 把圖的起點（START）連到 guardrail，表示每次執行都先進行 guardrail 檢查。
        builder.add_edge(START, "guardrail")
        builder.add_edge("guardrail", "agent")

        # tools_condition：AI 想用工具 → "tools"；直接回話 → post_process
        builder.add_conditional_edges(
            "agent",
            tools_condition,
            {"tools": "tools", END: END},
        )

        # 工具執行完 → 回到 agent 讓它根據結果繼續思考
        builder.add_edge("tools", "agent")

        return builder.compile()

    async def invoke(self, user_input: str) -> dict:
        """對外的主要進入點，回傳格式維持不變。"""
        result = await self._graph.ainvoke(
            {
                "messages": [HumanMessage(content=user_input)],
                "allow_rag": False,
            }
        )

        # 從 messages 取得最後的 AI 回覆
        last_msg = result["messages"][-1]
        response = (
            last_msg.content if isinstance(last_msg, AIMessage) else str(last_msg)
        )

        return {
            "response": response,
        }
