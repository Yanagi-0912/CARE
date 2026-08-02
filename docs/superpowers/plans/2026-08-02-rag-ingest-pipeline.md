# RAG Ingest Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 白名單 URL 可經 Firecrawl → chunk → embed → upsert Mongo；CLI 供人工審核後手動入庫。

**Architecture:** 純函式切塊＋`IngestService` 編排；腳本只做 composition root。先 embed 齊全再 delete+insert 同 url。

**Tech Stack:** Python 3.12、Motor、Gemini embeddings、FirecrawlClient、pytest／AsyncMock

**OpenSpec:** `openspec/changes/rag-ingest-pipeline/`

## Global Constraints

- 僅白名單（`app/services/rag/whitelist.is_allowed_url`）
- embedding 與 `settings.EMBEDDING_MODEL`／`MONGODB_VECTOR_DIM` 一致
- 同 URL：replace（非 append）
- **不要 git commit**（除非使用者另指示）
- 最小改動；不做 PDF／知識回報／Agent tool
- 工作目錄：`/Users/jamessu/Desktop/computersciencehomework/CARE`（目前 `jamesbranch`）

---

## File map

| File | Role |
|------|------|
| `app/services/rag/chunking.py` | `split_text_to_chunks` |
| `app/services/rag/ingest_service.py` | `IngestService` |
| `scripts/ingest_url.py` | CLI |
| `tests/unit/services/rag/test_chunking.py` | 切塊測試 |
| `tests/unit/services/rag/test_ingest_service.py` | 入庫測試 |

---

### Task 1: Chunking

**Files:** create `app/services/rag/chunking.py`, `tests/unit/services/rag/test_chunking.py`

- [ ] **Step 1: 失敗測試**

```python
from app.services.rag.chunking import split_text_to_chunks

def test_empty_returns_empty():
    assert split_text_to_chunks("") == []
    assert split_text_to_chunks("   ") == []

def test_short_text_single_chunk():
    assert split_text_to_chunks("高血壓宜低鈉飲食。") == ["高血壓宜低鈉飲食。"]

def test_long_text_splits():
    text = ("段落A。\n\n" * 5) + ("字" * 2000)
    chunks = split_text_to_chunks(text, max_chars=500, overlap=50)
    assert len(chunks) >= 2
    assert all(c.strip() for c in chunks)
```

- [ ] **Step 2: 實作** — 雙換行切段；單段 > max_chars 用滑窗；strip；丟空

- [ ] **Step 3: pytest**  
  `.venv/bin/python -m pytest -c pytest.ini tests/unit/services/rag/test_chunking.py -q --tb=short`

- [ ] **Step 4:** 勾 tasks 1.x；勿 commit

---

### Task 2: IngestService

**Files:** create `app/services/rag/ingest_service.py`, `tests/unit/services/rag/test_ingest_service.py`

- [ ] **Step 1: 失敗測試（重點）**

`IngestResult`: `status` (`ok`|`rejected`|`empty`|`error`), `url`, `chunk_count`, `message`

行為：
1. 非白名單 → `rejected`，不呼叫 scrape
2. scrape `""` → `empty`，不 write
3. 成功 → `aembed_documents` 後 `delete_many({"url": url})` + `insert_many`；docs 含 text/embedding/source_name/url/chunk_index
4. 第二次同 url：delete 再 insert；chunk_count = 新數量

Mock：`web_client.scrape`, `embeddings.aembed_documents`, collection with AsyncMock delete/insert.

Constructor 注入：`web_client`, `embeddings`, `collection`, `text_field="text"`, `vector_field="embedding"`, `vector_dim=None`（可選檢查維度）.

- [ ] **Step 2: 實作** 依 design（先建 docs 再 replace）

- [ ] **Step 3: pytest** `test_ingest_service.py` + `test_chunking.py`

- [ ] **Step 4:** 勾 tasks 2.x；勿 commit

---

### Task 3: CLI + 收尾

**Files:** create `scripts/ingest_url.py`

- [x] **Step 1: CLI**  
  asyncio 跑 `IngestService`；讀 `settings`；建 `FirecrawlClient`、`GoogleGenerativeAIEmbeddings`、Motor client；`--dry-run` 只 scrape+chunk+印數量不寫庫。

- [x] **Step 2: 回歸**  
  `.venv/bin/python -m pytest -c pytest.ini tests/unit/services/rag/test_chunking.py tests/unit/services/rag/test_ingest_service.py -q --tb=short`

- [x] **Step 3:** 勾 tasks 3–4；self-review

---

## Done when

- [x] OpenSpec tasks 全勾
- [x] 上述 pytest 綠
- [x] 未擅自 commit／未推 main
