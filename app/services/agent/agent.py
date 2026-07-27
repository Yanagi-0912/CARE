import logging
from typing import Optional
from langchain_core.messages import HumanMessage, AIMessage, AnyMessage, ToolMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition

from app.services.agent.utils.state import State
from app.services.agent.utils.nodes import AgentNodes
from app.services.agent.prompt import SYSTEM_PROMPT
from app.tools.registry import get_all_tools

logger = logging.getLogger(__name__)

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
        user_profile: Optional[dict] = None,
    ) -> dict:
        """對外的主要進入點，包含使用者的個人對話、歷史與個人健康檔案。"""
        if messages is None:
            messages = [HumanMessage(content=user_input)] if user_input else []
        elif user_input:
            # 確保目前的 user_input 存在於 messages 列表的最尾端，作為 AI 當前輪次的 Prompt
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

        # 從 messages 取得最後的 AI 回覆
        last_msg = result["messages"][-1]
        response = (
            last_msg.content if isinstance(last_msg, AIMessage) else str(last_msg)
        )
        if isinstance(response, list):
            response = "".join(
                (
                    part
                    if isinstance(part, str)
                    else (part.get("text", "") if isinstance(part, dict) else str(part))
                )
                for part in response
            )
        elif response is None:
            response = ""
        else:
            response = str(response)

        # 醫療工具會直接產出要送給 LINE 的內容，避免讓模型重新改寫 Flex JSON。
        medical_tool_names = {
            "find_nearby_hospitals",  # 搜尋附近醫療院所
            "lookup_medical_facility",  # 尋找特定醫療院所
            "request_location_quick_reply",  # 分享位置
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

        if not used_tool_names:
            logger.warning("[Agent] 未找到可用的醫療工具回覆，將沿用 AI 最終輸出")
        else:
            logger.info("[Agent] 醫療工具使用紀錄：%s", used_tool_names)

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

        logger.info(
            "[Agent] 執行完成，response_type=%s, call_request_location=%s",
            type(response).__name__,
            call_request_location,
        )

        return {
            "response": response,
            "call_request_location": call_request_location,
        }
