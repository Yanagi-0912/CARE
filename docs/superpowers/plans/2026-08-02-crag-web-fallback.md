# CRAG Web Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 知識庫空結果／CRAG 不足時在 `get_rag_answer` 內以既有 Firecrawl＋白名單 `WebSearchService` 自動 fallback，並自 Agent 工具集移除 `search_public_web`。

**Architecture:** `RagAnswerService` 注入可選 `web_search`；不足時 `await web_search.answer(query)`。Composition root 先建 `WebSearchService` 再注入 RAG。Registry 只暴露 `get_rag_answer`（+ medical tools）。

**Tech Stack:** FastAPI DI、LangChain tools、pytest／AsyncMock、既有 FirecrawlClient／whitelist。

**OpenSpec:** `openspec/changes/crag-web-fallback/`（proposal／design／specs／tasks 已齊）。

## Global Constraints

- 沿用現有 `WebSearchService`／Firecrawl／白名單，不重寫 client
- 空檢索、`incorrect`、ambiguous 耗盡 → web（選項 A）
- Agent registry SHALL NOT 含 `search_public_web`
- **不要 git commit**（使用者未要求）
- 繁體中文註解／使用者可見字串維持既有風格
- 最小改動；勿無關重構

---

## File map

| File | Role |
|------|------|
| `app/core/config.py`, `.env.example` | `RAG_WEB_FALLBACK_ENABLED` |
| `app/services/rag/answer_service.py` | fallback 邏輯 |
| `app/dependencies.py` | 注入順序 |
| `app/tools/registry.py` | 移除 web tool |
| `app/tools/rag_tools.py` | docstring |
| `tests/unit/services/rag/test_answer_service.py` | fallback 測試 |
| `tests/unit/tools/test_registry.py` | 工具集斷言 |
| `openspec/changes/crag-web-fallback/tasks.md` | 勾選進度 |

---

### Task 1: Config + RagAnswerService web fallback

**Files:**
- Modify: `app/core/config.py`, `.env.example`, `app/services/rag/answer_service.py`, `app/dependencies.py`
- Test: `tests/unit/services/rag/test_answer_service.py`

- [x] **Step 1: 寫失敗測試（TDD）**

在 `test_answer_service.py`：

1. 擴充 `_make_service` 接受 `web_search=None`, `web_fallback_enabled=True`。
2. 新增：
   - `test_empty_retrieve_calls_web_when_enabled`：docs=[]，mock `web_search.answer` → 回傳其結果並 `assert_awaited`
   - `test_crag_incorrect_calls_web`：grade INCORRECT → web
   - `test_crag_ambiguous_exhausted_calls_web`：ambiguous→incorrect → web
   - `test_web_fallback_disabled_keeps_no_hits`：有 mock web 但 `web_fallback_enabled=False` → `NO_HITS_MESSAGE` 且 web 未呼叫
3. 更新既有 `test_answer_returns_hits_message_when_no_docs`／`test_crag_incorrect_returns_no_hits_*`／`test_crag_ambiguous_rewrite_still_insufficient`：無注入 web 時行為不變（仍 `NO_HITS_MESSAGE`）。

- [x] **Step 2: 跑測試確認失敗**

```bash
cd CARE && python -m pytest tests/unit/services/rag/test_answer_service.py -q --tb=short
```

預期：新測試因缺少參數／未呼叫 web 而失敗。

- [x] **Step 3: 實作最小程式**

`config.py`（鄰近 `RAG_CRAG_ENABLED`）：

```python
RAG_WEB_FALLBACK_ENABLED: bool = os.getenv(
    "RAG_WEB_FALLBACK_ENABLED", "true"
).lower() in ("1", "true", "yes", "on")
```

`.env.example`：`RAG_WEB_FALLBACK_ENABLED=true`

`answer_service.py`：

```python
def __init__(..., web_search=None, web_fallback_enabled: bool = False):
    ...
    self.web_search = web_search
    self.web_fallback_enabled = bool(web_fallback_enabled and web_search is not None)

async def _web_or_no_hits(self, query: str) -> str:
    if not self.web_fallback_enabled:
        return NO_HITS_MESSAGE
    try:
        return await self.web_search.answer(query)
    except Exception:
        logger.exception("web fallback failed")
        return NO_ANSWER_MESSAGE  # 或 NO_HITS_MESSAGE；與 WebSearchService 失敗訊息一致即可
```

在 `answer()`：
- `if not ranked: return await self._web_or_no_hits(user_text)`
- CRAG 後 `if ranked is None: return await self._web_or_no_hits(user_text)`

`dependencies.py`：先建立 `_web_search_service`，再建 `_rag_answer_service(..., web_search=_web_search_service, web_fallback_enabled=settings.RAG_WEB_FALLBACK_ENABLED)`。可保留 `configure_web_tool`（測試仍可用）或停止呼叫——擇一，registry 不再用即可。

- [x] **Step 4: 跑測試通過**

```bash
cd CARE && python -m pytest tests/unit/services/rag/test_answer_service.py -q --tb=short
```

- [x] **Step 5: 勾選** `openspec/changes/crag-web-fallback/tasks.md` 1.1–1.3；**不要 commit**

---

### Task 2: 移除 Agent web tool + docstring

**Files:**
- Modify: `app/tools/registry.py`, `app/tools/rag_tools.py`, `tests/unit/tools/test_registry.py`
- Optional: `app/core/config.py` 註解「Firecrawl（WebSearchService／RAG fallback）」

- [x] **Step 1: 改 registry 測試**

- `include_rag_tool=True` → 有 `get_rag_answer`，**無** `search_public_web`
- 刪除或改寫 `test_get_all_tools_can_toggle_web_independently`（參數可移除）

- [x] **Step 2: 實作**

`registry.py`：移除 `search_public_web` import 與 `include_web_tool` 邏輯。

`rag_tools.py` docstring 範例：

```python
"""當問題需要引用醫療知識庫（必要時會補充允許網域的公開網路資料）時呼叫。
例如疾病照護建議、症狀處置原則、慢病管理等。
"""
```

- [x] **Step 3: 跑測試**

```bash
cd CARE && python -m pytest tests/unit/tools/test_registry.py tests/unit/tools/test_web_tools.py tests/unit/services/rag/test_answer_service.py -q --tb=short
```

`test_web_tools` 可保留（函式仍存在）。

- [x] **Step 4: 勾選 tasks 2.1–2.3；不要 commit**

---

### Task 3: 回歸與收尾

- [x] **Step 1: 跑較廣 unit**

```bash
cd CARE && python -m pytest tests/unit/services/rag tests/unit/tools tests/unit/services/agent -q --tb=line
```

- [x] **Step 2: 勾選 tasks 3.x–4.x**

- [x] **Step 3: Self-review** — 確認 empty／incorrect／ambiguous→web；flag off／無注入→舊訊息；registry 無 web tool

---

## Done when

- [x] OpenSpec artifacts valid
- [x] 上述 pytest 綠
- [x] `tasks.md` 全勾
- [x] 未擅自 commit
