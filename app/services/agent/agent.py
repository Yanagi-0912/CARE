from typing import Optional
from langchain_core.messages import HumanMessage, AIMessage, AnyMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition

from app.services.agent.utils.state import State
from app.services.agent.utils.nodes import AgentNodes
from app.services.agent.prompt import SYSTEM_PROMPT
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

    async def invoke(
        self,
        user_input: str = "",
        messages: Optional[list[AnyMessage]] = None,
    ) -> dict:
        """對外的主要進入點，回傳格式維持不變。"""
        if messages is None:
            messages = [HumanMessage(content=user_input)]

        result = await self._graph.ainvoke(
            {
                "messages": messages,
                "allow_rag": False,
            }
        )

        # 從 messages 取得最後的 AI 回覆
        last_msg = result["messages"][-1]
        response = (
            last_msg.content if isinstance(last_msg, AIMessage) else str(last_msg)
        )

        # 防禦性後置處理：若呼叫了 get_rag_answer，但 AI 的最終回覆中遺漏了「參考資料來源」，則自動由工具輸出中提取並後補。
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

        # 檢測是否調用了位置請求工具
        call_request_location = False
        for msg in result.get("messages", []):
            if getattr(msg, "name", None) == "request_location_quick_reply":
                call_request_location = True
                break

        return {
            "response": response,
            "call_request_location": call_request_location,
        }
