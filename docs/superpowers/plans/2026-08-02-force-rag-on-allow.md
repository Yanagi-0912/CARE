# Force RAG on allow_rag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** `allow_rag=True` 且模型未呼叫工具時，強制注入一次 `get_rag_answer`。

**Work dir:** `/Users/jamessu/Desktop/computersciencehomework/CARE`  
**OpenSpec:** `openspec/changes/force-rag-on-allow/`

## Global Constraints

- 只改 `nodes.py` + agent 單元測試（可抽小 helper 同檔或同目錄）
- 禁止 monkey patch 全域；mock LLM via DI
- DO NOT commit（controller commits）
- 防迴圈：messages 已有 `ToolMessage` name=`get_rag_answer` 則不強制

---

### Task 1: Force RAG in agent_node

**Files:**
- Modify: `app/services/agent/utils/nodes.py`
- Create: `tests/unit/services/agent/test_force_rag.py`

- [ ] **Step 1: 寫失敗測試** `tests/unit/services/agent/test_force_rag.py`

```python
import pytest
from unittest.mock import MagicMock, AsyncMock
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
from app.services.agent.utils.nodes import AgentNodes
from app.tools.registry import get_all_tools


@pytest.mark.asyncio
async def test_force_rag_when_allow_rag_and_no_tool_calls(monkeypatch):
    # 確保 get_all_tools(True) 含 get_rag_answer；若測試環境 DI 未 configure，
    # 可 monkeypatch get_all_tools 回傳帶 name=get_rag_answer 的 MagicMock tools
    # （僅 patch nodes 模組內的 get_all_tools 引用，不要 patch 全域服務實例）
    ...
```

具體案例：
1. `allow_rag=True`，LLM 回 `AIMessage(content="腦補")` 無 tool_calls → 結果 AIMessage 有 tool_calls，name=`get_rag_answer`，args.query=`我有六隻腳趾頭`
2. messages 已含 `ToolMessage(name="get_rag_answer", content="...")` + LLM 無 tool_calls → 不注入
3. `allow_rag=False` → 不注入

註：OpenSpec／tasks 禁止 monkey patch「修改全域／別處導入之實例」；patch `nodes.get_all_tools` 回傳假 tool 清單是可接受的測試隔離方式。若專案已有 `configure_rag_tool` 可走真實 registry，優先用之。

- [ ] **Step 2:** pytest 該檔 FAIL

- [ ] **Step 3: 實作** 在 `agent_node` 於 `ainvoke` 之後：

```python
# 偽碼
if (
    state.get("allow_rag")
    and "get_rag_answer" in tool_names
    and not tool_calls
    and not _already_ran_rag(state["messages"])
):
    user_text = _latest_human_text(state["messages"])
    response = AIMessage(
        content="",
        tool_calls=[{
            "name": "get_rag_answer",
            "args": {"query": user_text},
            "id": "forced_rag_1",
            "type": "tool_call",
        }],
    )
    called = ["get_rag_answer"]
    force_rag = True
else:
    force_rag = False

log_stage(..., call=called or None, force_rag=force_rag or None, ...)
```

Helper：`_already_ran_rag`、`_latest_human_text` 可放同檔。

- [ ] **Step 4:** pytest `tests/unit/services/agent/ -q` 全綠

- [ ] **Step 5:** 勾選 openspec tasks；不 commit；回報 DONE
