from __future__ import annotations

import logging
import time
from typing import Any, Optional

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from langgraph.prebuilt import ToolNode, tools_condition

from app.core.request_logging import log_stage, stage_timer
from app.i18n.messages import (
    split_at_sources_heading,
    t,
    strip_sources_section,
    text_contains_sources_heading,
)
from app.services.agent.utils.nodes import AgentNodes
from app.services.agent.utils.state import State
from app.services.medical.symptom_classification.urgency import (
    URGENCY_EMERGENCY,
    URGENCY_NONE,
)
from app.services.gemini.shared.parser import content_to_text
from app.services.rag.fail_messages import is_rag_fail
from app.tools.user_document_tools import is_document_answer_unavailable
from app.tools.registry import get_all_tools

logger = logging.getLogger(__name__)

_RAG_TOOL_NAME = "get_rag_answer"


def _trailing_tool_messages(messages: list[AnyMessage]) -> list[ToolMessage]:
    """這一輪 tools 節點剛剛附加上去的 ToolMessage。

    從尾端往回收集而不是掃全部：多輪對話的 messages 裡含著先前每一輪的
    ToolMessage，掃全部會把上一輪的工具也算進來，於是「這一輪只用了 RAG」
    這個判斷在第二次提問時就永遠成立不了。
    """
    collected: list[ToolMessage] = []
    for msg in reversed(messages or []):
        if not isinstance(msg, ToolMessage):
            break
        collected.append(msg)
    collected.reverse()
    return collected


def _route_after_tools(state: State) -> str:
    """工具跑完後：直通，還是回去讓模型組裝回覆。

    **為什麼要有直通這條路**：ReAct 的預設假設是「工具回傳原料、模型負責翻成
    人話」，所以工具輸出一律會被丟回模型再處理一次。但 `get_rag_answer` 內部
    自己就跑了一次生成，回傳的是成品——含本文、行內引用與來源清單。再問模型
    一次等於把成品拆開重做。

    線上實測（care-dev，7 天）：那一次呼叫 p50 3,205ms，是整條管線第二大的
    一塊，僅次於 RAG 生成本身的 6.2s。三題 A/B 顯示它做三件事——移除行內引用、
    加醫療提醒、重寫措辭——而醫療提醒是不可靠的（兩輪實測分別只加了 2/3 與
    0/3）。三件事現在都由程式處理：引用保留（Flex 的來源按鈕標籤就是
    「[1] 來源名」，留著才對得起來）、提醒改成固定文案（100%）、措辭直接在
    RAG 自己的 prompt 裡要求。

    **條件刻意收得很緊**，只有「這一輪只呼叫了 get_rag_answer 一個工具、而且
    它成功產出答案」才直通：

    - **多工具同時呼叫**（例如又查核又 RAG）必須回模型，因為只有它能把兩份
      輸出合成一段話。
    - **RAG 失敗訊息**（`is_rag_fail`）不直通。那些文案是給模型當素材用的
      錯誤說明，直接丟給使用者會漏掉既有的降級話術。
    - 其餘工具（附近院所、查核卡）本來就有自己的直通路徑，不經過這裡。

    已知取捨：多輪追問（「那芒果呢」）時，模型那一步看得到完整對話歷史，
    直通看不到——送出的是 RAG 工具針對單一 query 寫的答案。
    """
    # 本輪沒提供 RAG 就不可能有合法的 RAG 結果可直通；會走到這裡的只剩被
    # `_execute_offered_tools` 攔下的呼叫。
    if not state.get("allow_rag"):
        return "agent"
    tool_messages = _trailing_tool_messages(state.get("messages") or [])
    if len(tool_messages) != 1:
        return "agent"
    only = tool_messages[0]
    if getattr(only, "name", None) != _RAG_TOOL_NAME:
        return "agent"
    # ToolNode 出錯時（例如模型把參數名 query 猜成 question）回的是
    # 「Error invoking tool …」——直通會把這段錯誤原樣送給使用者。
    if getattr(only, "status", None) == "error":
        return "agent"
    if is_rag_fail(content_to_text(only.content)):
        return "agent"
    return "rag_direct"


def _insert_before_sources(answer: str, notice: str) -> str:
    """把提醒插在「參考資料來源」標題之前，沒有來源段落時直接接在最後。

    位置是關鍵而不是美觀問題：`reply.py._build_answer_card` 組卡片時會呼叫
    `strip_sources_section`，而它回傳的是**來源標題之前**的全部內容。提醒
    若接在整段最後面，純文字回覆看得到，卡片卻永遠看不到——而卡片才是絕大
    多數使用者實際看到的東西。
    """
    split = split_at_sources_heading(answer)
    if split is None:
        return f"{answer}\n\n{notice}"
    heading, sources_body = split
    before, _ = answer.split(heading, 1)
    return f"{before.rstrip()}\n\n{notice}\n\n{heading}{sources_body}"


def _rag_direct_reply_node(state: State) -> dict:
    """把 RAG 工具的答案原樣當成最終回覆，只補上醫療提醒。

    刻意**不**移除行內的 `[1][2][3]`：Flex 卡的來源按鈕標籤就是「[1] 來源名」
    （見 rag_answer_flex._source_buttons），留著行內編號才對得起來。經模型
    重寫的版本會把它們拿掉，那反而讓句子與按鈕失去對應。

    醫療提醒改成固定文案而非依賴模型：system prompt 第 4 條要求遇醫療緊急
    情況提醒尋求專業協助，但三題實測只加了兩題。已含提醒時不重複附加——
    判斷用整串比對而非關鍵字，避免答案本文碰巧提到「就醫」就被誤判。
    """
    messages = state.get("messages") or []
    tool_messages = _trailing_tool_messages(messages)
    answer = content_to_text(tool_messages[-1].content).strip()

    notice = t("rag.professional_advice_notice")
    if notice and notice not in answer:
        answer = _insert_before_sources(answer, notice)

    # 前綴由程式附加，而不是像不直通時那樣要求模型寫（prompt.py 規則）。
    # 兩個理由：
    #   1. 模型寫的版本字串不固定，`strip_rag_prefix` 剝不掉，於是卡片第一行
    #      會出現「根據 RAG 資訊，」這種內部術語——使用者不知道 RAG 是什麼。
    #   2. 固定字串才能被剝除，行為與今天一致：卡片路徑剝掉（header 與來源
    #      按鈕已經承擔「這段有外部來源」的告知），純文字路徑保留（那條路徑
    #      沒有其他標記）。
    prefix = t("agent.rag_prefix")
    if prefix and not answer.lstrip().startswith(prefix):
        answer = f"{prefix}\n{answer}"

    log_stage(logger, "rag_direct_reply", chars=len(answer))
    return {"messages": [AIMessage(content=answer)]}


# LangGraph 基本概念：
# - State：流程共用資料（例如 messages、allow_rag）
# - Node：每一步要做的事（函式）
# - Edge：定義下一步走向
# - START / END：流程起點與終點
# 執行時會依邊的定義由 START 流向各節點，最後到 END。

TOOL_RESULT_PREVIEW_LEN = 120

# 一輪對話最多幾次「agent → tools」往返。3 是從程式裡的合法路徑數出來的：
#   1. 模型自己選的工具（同一次可呼叫多個，仍算一次往返）。
#   2. 工具回來後模型直接回話，但 `agent_node` 的 force_rag 條件成立，強制
#      補一次 `get_rag_answer`（nodes.py；例如先 verify_claim 再補 RAG）。
#   3. 某次呼叫被 `_execute_offered_tools` 攔下後，模型改呼叫有提供的工具。
# prompt 的工具規則 (a)-(i) 都是一個意圖對一個工具，沒有要求串接。
MAX_TOOL_ROUNDS = 3

# 最長合法路徑的節點數：guardrail＋agent，之後每次往返是 tools＋agent（或以
# rag_direct 收尾），2 + 2×3 = 8。recursion_limit 不是節點數本身：實測
# LangGraph 1.1.10 上 N 個節點的路徑要 limit ≥ N+1 才跑得完（只有
# guardrail→agent 的 2 節點路徑，limit=2 就會丟錯），所以再加 1。
# 這個對應由 tests/unit/services/agent/test_recursion_limit.py 釘住：
# 升級 LangGraph 後計步方式若改變，那裡會先壞。
#
# 必須明確傳入：不傳時用的是 LangGraph 1.1.10 的預設 10007（不是舊版的 25，
# 見 langgraph/_internal/_config.py），模型卡在「呼叫 → 被攔 → 再呼叫」時
# 等於沒有上限，每一步都是一次 Gemini 呼叫。超過時丟 GraphRecursionError，
# 由 message_handler 回 line.fallback_process_error。
AGENT_RECURSION_LIMIT = 2 + 2 * MAX_TOOL_ROUNDS + 1


# 被攔下的工具呼叫回給模型的內容。刻意**不**提參數名或用法：2026-09-10 的
# 事件裡，ToolNode 回的參數驗證錯誤（「query: Field required, Please fix the
# error」）等於把正確的呼叫方式教給了模型，它照著改完就成功繞過了 guardrail。
_BLOCKED_TOOL_REPLY = "此工具本輪不可用。請不要再呼叫未提供的工具，直接依對話內容回覆使用者。"


def _offered_tool_names(state: State) -> set[str]:
    """這一輪實際綁給模型的工具——與 agent_node 用同一個判斷，兩邊不會分岔。"""
    return {
        tool.name
        for tool in get_all_tools(include_rag_tool=bool(state.get("allow_rag", False)))
    }


async def _execute_offered_tools(state: State, config: Any, tool_executor: Any) -> dict:
    """只執行本輪有提供給模型的工具，其餘回拒絕訊息。

    **為什麼執行層也要把關**：guardrail 的決定原本只在「綁定工具」那一步生效。
    執行工具的 ToolNode 是用全部工具建的（它必須認得每一種可能被呼叫的工具），
    於是只要模型輸出一個沒綁給它的呼叫，ToolNode 就照跑。2026-09-10 的線上
    日誌逐步記下了這件事：

        stage=guardrail allow_rag=False
        stage=agent_decide tools=[6 個，無 get_rag_answer] call=['get_rag_answer']
        stage=tool_result  Error … kwargs {'question': '法國國歌'} … query: Field required
        stage=agent_decide call=['get_rag_answer']        ← 照錯誤訊息改好參數重試
        stage=tool_result  has_sources=True 以下參考網路公開資料…

    模型從 system prompt 裡知道這個工具的名字（prompt 點名它十幾次），但沒有
    它的規格，所以先猜錯參數名；錯誤訊息把正確名稱告訴它，第二次就成功了。
    之後網搜降級自動建報告，130 個非醫療 chunk 經核准進了知識庫。

    攔下的呼叫回 `status="error"` 的 ToolMessage：每個 tool_call 都必須有對應
    的回應，否則下一次呼叫模型時對話結構不合法；標成 error 則讓
    `_route_after_tools` 不會把拒絕訊息當成答案直通給使用者。

    沒有任何呼叫被攔時，行為與導入前逐位元相同——直接把原 state 交給 ToolNode。

    殘餘風險：模型收到拒絕後仍可能再呼叫一次。那會一路被攔，直到
    `AGENT_RECURSION_LIMIT` 中止這一輪；拒絕訊息已明確要求它直接回覆。
    """
    names = _tool_names_from_state(state)
    t0 = time.perf_counter()
    log_stage(logger, "tools_start", names=names)

    messages = list(state.get("messages") or [])
    last = messages[-1] if messages else None
    tool_calls = list(getattr(last, "tool_calls", None) or [])
    offered = _offered_tool_names(state)
    allowed = [tc for tc in tool_calls if tc.get("name") in offered]
    blocked = [tc for tc in tool_calls if tc.get("name") not in offered]

    if blocked:
        log_stage(
            logger,
            "tool_blocked",
            names=[tc.get("name") for tc in blocked],
            allow_rag=bool(state.get("allow_rag")),
        )

    if not allowed:
        if not blocked:
            # 沒有任何工具呼叫：交給 ToolNode 照舊處理（它會自行報錯）。
            return await tool_executor.ainvoke(state, config=config)
        return {"messages": [_blocked_message(tc) for tc in blocked]}

    exec_state = state
    if blocked:
        exec_state = {**state, "messages": [*messages[:-1], last.model_copy(update={"tool_calls": allowed})]}

    try:
        result = await tool_executor.ainvoke(exec_state, config=config)
    except Exception:
        log_stage(logger, "tools_fail", names=names, ms=int((time.perf_counter() - t0) * 1000))
        raise

    executed = (result.get("messages") if isinstance(result, dict) else None) or []
    _log_tool_result_summaries(executed, ms=int((time.perf_counter() - t0) * 1000), names=names)
    if not blocked:
        return result

    # 依模型原本的呼叫順序排好回應，攔下的與執行的交錯時也對得上。
    replies = {getattr(m, "tool_call_id", None): m for m in executed}
    replies.update({tc["id"]: _blocked_message(tc) for tc in blocked})
    call_ids = {tc.get("id") for tc in tool_calls}
    ordered = [replies[tc["id"]] for tc in tool_calls if tc.get("id") in replies]
    extras = [m for m in executed if getattr(m, "tool_call_id", None) not in call_ids]
    return {"messages": ordered + extras}


def _blocked_message(tool_call: dict) -> ToolMessage:
    return ToolMessage(
        content=_BLOCKED_TOOL_REPLY,
        name=tool_call.get("name"),
        tool_call_id=tool_call["id"],
        status="error",
    )


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
        text = content_to_text(getattr(msg, "content", ""))
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


def _urgency_condition(state: State) -> str:
    """急迫度為 emergency 時繞過 agent，直接走緊急flex message。"""
    return "emergency" if state.get("urgency") == URGENCY_EMERGENCY else "agent"


class Agent:
    def __init__(self, llm, guardrail_service, urgency_classifier=None) -> None:
        self._llm = llm
        self._guardrail_service = guardrail_service
        self._urgency_classifier = urgency_classifier
        self._graph = self._build_graph()

    def _build_graph(self):
        """建立並編譯 LangGraph（原子化節點模式）"""
        builder = StateGraph(State)

        nodes = AgentNodes(
            llm=self._llm,
            guardrail_service=self._guardrail_service,
            urgency_classifier=self._urgency_classifier,
        )

        all_tools = get_all_tools(include_rag_tool=True)
        tool_executor = ToolNode(all_tools)

        async def tools_node(state: State, config: RunnableConfig) -> dict:
            return await _execute_offered_tools(state, config, tool_executor)

        builder.add_node("guardrail", nodes.guardrail_node)
        builder.add_node("emergency", nodes.emergency_node)
        builder.add_node("agent", nodes.agent_node)
        builder.add_node("tools", tools_node)
        builder.add_node("rag_direct", _rag_direct_reply_node)

        builder.add_edge(START, "guardrail")
        # 急迫度短路：判定為緊急時直接產生卡片，不進 agent。安全檢查不能是 agent
        # 可以選擇不做的事——前一版把它放在工具裡，agent 選了 RAG，檢查就從未
        # 執行過。
        builder.add_conditional_edges(
            "guardrail",
            _urgency_condition,
            {"emergency": "emergency", "agent": "agent"},
        )
        builder.add_edge("emergency", END)
        builder.add_conditional_edges(
            "agent",
            tools_condition,
            {"tools": "tools", END: END},
        )
        builder.add_conditional_edges(
            "tools",
            _route_after_tools,
            {"rag_direct": "rag_direct", "agent": "agent"},
        )
        builder.add_edge("rag_direct", END)

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

        with stage_timer(logger, "agent_graph") as timing:
            result = await self._graph.ainvoke(
                {
                    "messages": messages,
                    "allow_rag": False,
                    "urgency": URGENCY_NONE,
                    "urgency_display": "",
                    "user_profile": user_profile,
                },
                config={"recursion_limit": AGENT_RECURSION_LIMIT},
            )
            timing["msgs"] = len(result.get("messages") or ())

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
            # 症狀科別建議卡。除了 Flex JSON 不能被改寫之外，這裡還有安全理由：
            # 紅旗卡刻意不含任何門診科別，讓模型重寫有可能把「請立即就醫」稀釋
            # 成「可以考慮掛某某科」，那正是本功能要避免的失效模式。
            "suggest_department_for_symptom",
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
        #
        # used_tool_names 非空代表 response 已被上面的醫療工具接管，內容是要原封
        # 不動送給 LINE 的 Flex JSON。此時後補來源會把文字接在 JSON 尾巴後面，讓
        # reply.py 的 `_try_parse_flex_message`（要求整串以 "{" 開頭、"}" 結尾）
        # 解析失敗，整張卡片退化成使用者看得到的一整段裸 JSON——模型同一輪既呼叫
        # verify_claim 又呼叫 get_rag_answer 時就會踩到。後補來源只對「模型自己寫
        # 出來的自然語言回覆」有意義，因此這裡以「有沒有被覆寫」為準，而不是回頭
        # 猜 response 像不像 JSON：覆寫與否正是問題的成因，判斷它才不會漏掉未來
        # 新增的其他 Flex 工具。
        #
        # 急迫度短路時 response 是緊急卡的 Flex JSON，理由與上同，一併跳過。
        is_emergency = result.get("urgency") == URGENCY_EMERGENCY
        rag_tool_content = None
        if not used_tool_names and not is_emergency:
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

        # 呈現層要知道「這輪是不是有內容可以做成卡片」。判斷放在這裡而非
        # reply.py，因為只有這裡看得到 ToolMessage。
        answer_kind: str | None = None
        if not used_tool_names and not is_emergency:
            # used_tool_names 非空代表 response 已被醫療工具接管、內容是要原封
            # 不動送出的 Flex JSON，再組一次卡只會壞掉（同「後補來源」那段的
            # 理由）。緊急卡同理。
            for msg in reversed(result.get("messages", [])):
                name = getattr(msg, "name", None)
                if name not in ("get_rag_answer", "answer_from_uploaded_document"):
                    continue
                content = (
                    msg.content if isinstance(msg.content, str) else str(msg.content)
                )
                if name == "get_rag_answer":
                    answer_kind = None if is_rag_fail(content) else "rag"
                else:
                    answer_kind = (
                        None if is_document_answer_unavailable(content) else "document"
                    )
                break

        call_request_location = False
        for msg in result.get("messages", []):
            if getattr(msg, "name", None) == "request_location_quick_reply":
                call_request_location = True
                break

        logger.info(
            "[Agent] 執行完成，response_type=%s, call_request_location=%s, "
            "answer_kind=%s, emergency=%s",
            type(response).__name__,
            call_request_location,
            answer_kind,
            is_emergency,
        )

        return {
            "response": response,
            "call_request_location": call_request_location,
            "answer_kind": answer_kind,
            # 供呼叫端決定要不要通報家人。回傳判定與說明而不是「要不要通報」，
            # 因為「誰該收到」是家庭授權的事，不屬於 agent。
            "emergency": is_emergency,
            "emergency_reason": result.get("urgency_display") or "",
        }
