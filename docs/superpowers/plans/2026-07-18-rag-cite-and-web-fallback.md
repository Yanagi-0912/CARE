# RAG Cite 修復與 Web Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修復 RAG 參考來源斷號與「無法回答仍附 KB 來源」的信任 bug，並在 KB 無命中／內容不足時以 Firecrawl 白名單 Web Fallback 補齊答案。

**Architecture:** 維持單一工具 `get_rag_answer`；orchestration 全放在 `RagAnswerService`（retrieve → KB 生成 → 啟發式判定 → 必要時 Web Search+Scrape → cite）。新增可注入的 `WebSearchClient` Protocol 與 `FirecrawlClient` 實作；白名單網域寫死常數；組裝僅在 `app/dependencies.py`。

**Tech Stack:** Python 3.12、FastAPI、LangChain `Document`、Gemini（既有）、httpx（呼叫 Firecrawl v1 REST）、pytest + pytest-asyncio、Dependency Injection（禁止 monkey patch）。

**OpenSpec change:** `openspec/changes/rag-cite-and-web-fallback/`（proposal / design / specs/rag-responses / tasks）

## Global Constraints

- 測試禁止 `unittest.mock.patch`／monkey patch；外部依賴一律 constructor 注入 fake／mock 實例
- LINE 回覆維持純文字，不得輸出 Markdown 連結語法
- 來源最多 3 筆、編號連續；KB 與 Web 不得混用於同一則回答
- Web Fallback 永久啟用，無 feature flag；無金鑰或呼叫失敗時降級為無法回答、不附來源
- 白名單後綴寫死：`gov.tw`、`hpa.gov.tw`、`cdc.gov.tw`、`mohw.gov.tw`（含子網域）；不進 `.env`
- Commit／面向使用者說明使用繁體中文
- DoD：相關單元測試全綠；完成後勾選 `openspec/changes/rag-cite-and-web-fallback/tasks.md`

## File Structure

| Path | Responsibility |
| --- | --- |
| `app/services/rag/answer_service.py` | Cite、無法回答判定、KB／Web orchestration |
| `app/services/rag/web_client.py` | `WebSearchHit` dataclass + `WebSearchClient` Protocol |
| `app/services/rag/whitelist.py` | 網域白名單常數與 `is_allowed_url` |
| `app/services/rag/firecrawl_client.py` | Firecrawl v1 Search／Scrape 實作（注入 httpx client） |
| `app/services/rag/__init__.py` | 匯出新常數／型別（若測試需要） |
| `app/core/config.py` | `FIRECRAWL_API_KEY` |
| `app/dependencies.py` | 組裝 `FirecrawlClient` 注入 `RagAnswerService` |
| `.env.example` | 補上 `FIRECRAWL_API_KEY` 說明 |
| `tests/unit/services/rag/test_answer_service.py` | Cite、無法回答、Web Fallback 路徑 |
| `tests/unit/services/rag/test_web_whitelist.py` | 白名單允許／拒絕 |
| `tests/unit/services/rag/test_firecrawl_client.py` | Firecrawl 契約（fake httpx，不打真實網路） |

非本次範圍：知識回報／入庫、弱命中分數門檻、KB+Web 混用 cite、feature flag、Admin、改 embedding。

---

### Task 1: 參考來源連續編號（Cite 修復）

**Files:**
- Modify: `app/services/rag/answer_service.py`（`_append_sources`）
- Test: `tests/unit/services/rag/test_answer_service.py`
- OpenSpec: tasks 1.1、1.2

**Interfaces:**
- Consumes: 既有 `Document`、`CITE_TOP_K`
- Produces: `_append_sources(answer_text: str, docs: list[Document], *, source_kind: str = "kb") -> str` — 僅對通過過濾後的來源從 1 連續編號；`source_kind=="web"` 時行格式為 `[n] 網路：{name}：{url}`（本 task 先支援參數，Web 路徑於 Task 5 使用）

- [ ] **Step 1: 寫失敗測試（斷號與連續編號）**

在 `tests/unit/services/rag/test_answer_service.py` 新增：

```python
def test_append_sources_renumbers_after_skipping_missing_and_duplicate_urls():
    docs = [
        Document(page_content="a", metadata={"source_name": "缺網址", "url": ""}),
        Document(
            page_content="b",
            metadata={
                "source_name": "國健署",
                "url": "https://www.hpa.gov.tw/a",
            },
        ),
        Document(
            page_content="c",
            metadata={
                "source_name": "重複",
                "url": "https://www.hpa.gov.tw/a",
            },
        ),
        Document(
            page_content="d",
            metadata={
                "source_name": "疾管署",
                "url": "https://www.cdc.gov.tw/b",
            },
        ),
    ]
    result = RagAnswerService._append_sources("答案正文", docs)
    assert "參考資料來源：" in result
    assert "[1] 國健署：https://www.hpa.gov.tw/a" in result
    assert "[2] 疾管署：https://www.cdc.gov.tw/b" in result
    assert "[3]" not in result
    assert "缺網址" not in result


def test_append_sources_web_kind_prefixes_network_label():
    docs = [
        Document(
            page_content="x",
            metadata={
                "source_name": "衛福部",
                "url": "https://www.mohw.gov.tw/x",
            },
        )
    ]
    result = RagAnswerService._append_sources(
        "答案正文", docs, source_kind="web"
    )
    assert "[1] 網路：衛福部：https://www.mohw.gov.tw/x" in result
```

- [ ] **Step 2: 跑測試確認失敗**

Run（於 CARE repo 根目錄、已啟用 `.venv`）：

```bash
python -m pytest tests/unit/services/rag/test_answer_service.py::test_append_sources_renumbers_after_skipping_missing_and_duplicate_urls tests/unit/services/rag/test_answer_service.py::test_append_sources_web_kind_prefixes_network_label -v
```

Expected: FAIL（斷號仍出現，或 `source_kind` 未支援／AssertionError）

- [ ] **Step 3: 最小實作 `_append_sources`**

將 `app/services/rag/answer_service.py` 的 `_append_sources` 改為：

```python
@staticmethod
def _append_sources(
    answer_text: str,
    docs: list[Document],
    *,
    source_kind: str = "kb",
) -> str:
    source_lines: list[str] = []
    seen_urls: set[str] = set()

    for doc in docs:
        if len(source_lines) >= CITE_TOP_K:
            break
        source_name = str(doc.metadata.get("source_name") or "").strip()
        url = str(doc.metadata.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        display_idx = len(source_lines) + 1
        if source_kind == "web":
            label = source_name if source_name else url
            source_lines.append(f"[{display_idx}] 網路：{label}：{url}")
        elif source_name:
            source_lines.append(f"[{display_idx}] {source_name}：{url}")
        else:
            source_lines.append(f"[{display_idx}] {url}")

    if not source_lines:
        return answer_text
    return f"{answer_text}\n\n參考資料來源：\n" + "\n".join(source_lines)
```

注意：不要再用 `enumerate(docs[:CITE_TOP_K], start=1)` 當顯示編號；改為「先過濾再編號」，候選可掃完整 `docs` 直到湊滿 `CITE_TOP_K`。

- [ ] **Step 4: 跑測試確認通過**

```bash
python -m pytest tests/unit/services/rag/test_answer_service.py::test_append_sources_renumbers_after_skipping_missing_and_duplicate_urls tests/unit/services/rag/test_answer_service.py::test_append_sources_web_kind_prefixes_network_label tests/unit/services/rag/test_answer_service.py::test_answer_puts_all_docs_in_prompt_but_cites_top_3_only -v
```

Expected: PASS（既有 top-3 cite 測試也應仍綠）

- [ ] **Step 5: Commit**

```bash
git add app/services/rag/answer_service.py tests/unit/services/rag/test_answer_service.py
git commit -m "$(cat <<'EOF'
fix(rag): 參考來源改為過濾後連續編號

EOF
)"
```

完成後勾選 `openspec/changes/rag-cite-and-web-fallback/tasks.md` 的 1.1、1.2。

---

### Task 2: 「無法回答」啟發式 — 不附 KB 來源

**Files:**
- Modify: `app/services/rag/answer_service.py`
- Test: `tests/unit/services/rag/test_answer_service.py`
- OpenSpec: tasks 1.3、1.4

**Interfaces:**
- Consumes: Task 1 的 `_append_sources`
- Produces:
  - `CANNOT_ANSWER_MARKERS: tuple[str, ...]`
  - `_is_cannot_answer(text: str) -> bool`
  - `answer()`：若 KB 生成判定無法回答，**本 task 先回傳純文字答案且不附來源**（Web Fallback 於 Task 5 接上；本 task 測試僅鎖定「不附參考資料來源」）

- [ ] **Step 1: 寫失敗測試**

```python
@pytest.mark.parametrize(
    "answer_content",
    [
        "我不知道這個問題的答案。",
        "根據現有資料無法提供建議。",
        "未找到足夠資訊。",
        "找不到相關的衛教說明。",
    ],
)
@pytest.mark.asyncio
async def test_answer_omits_kb_sources_when_model_cannot_answer(answer_content):
    docs = [
        Document(
            page_content="無關片段",
            metadata={
                "source_name": "國健署",
                "url": "https://www.hpa.gov.tw/x",
            },
        )
    ]
    svc, _gemini, _retriever = _make_service(
        docs=docs, answer_content=answer_content
    )
    result = await svc.answer("某個冷門問題")
    assert "參考資料來源" not in result
    assert "https://www.hpa.gov.tw/x" not in result
```

同時新增 helper／單元測試（可放同一檔）：

```python
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("正常可回答的衛教內容", False),
        ("我不知道", True),
        ("無法提供相關資訊", True),
        ("", True),
        ("   ", True),
    ],
)
def test_is_cannot_answer_heuristic(text, expected):
    assert RagAnswerService._is_cannot_answer(text) is expected
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
python -m pytest tests/unit/services/rag/test_answer_service.py::test_answer_omits_kb_sources_when_model_cannot_answer tests/unit/services/rag/test_answer_service.py::test_is_cannot_answer_heuristic -v
```

Expected: FAIL（目前仍會 `_append_sources`，或 `_is_cannot_answer` 不存在）

- [ ] **Step 3: 最小實作**

在 `answer_service.py` 加入：

```python
CANNOT_ANSWER_MARKERS: tuple[str, ...] = (
    "不知道",
    "無法",
    "未找到",
    "找不到相關",
)

NO_ANSWER_MESSAGE = "目前無法提供相關資訊，請稍後再試或換一種方式描述問題。"
```

```python
@staticmethod
def _is_cannot_answer(text: str) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return True
    return any(marker in normalized for marker in CANNOT_ANSWER_MARKERS)
```

調整 `answer()`（本 task 尚不呼叫 web；無 docs 行為暫維持 `NO_HITS_MESSAGE`，Task 5 再改）：

```python
async def answer(self, user_text: str) -> str:
    docs = await self.retriever.ainvoke(user_text)
    if not docs:
        return NO_HITS_MESSAGE

    context = "\n".join(
        f"{idx}. {doc.page_content}" for idx, doc in enumerate(docs, start=1)
    )
    messages = RAG_PROMPT.format_messages(question=user_text, context=context)
    rag_result = await self.gemini_service.chat_model.ainvoke(messages)
    answer_text = rag_result.content or "抱歉，我目前找不到相關資料，請稍後再試。"
    if not isinstance(answer_text, str):
        answer_text = str(answer_text)

    if self._is_cannot_answer(answer_text):
        return answer_text

    return self._append_sources(answer_text, docs, source_kind="kb")
```

注意：既有 `test_answer_uses_default_message_when_model_returns_empty_text` 期望空字串變成預設句且**無**「參考資料來源」。預設句含「找不到」會被 `_is_cannot_answer` 判為 True，行為與「不附來源」一致；若該測試仍 assert 完整等於預設句，應維持通過。若失敗，把該測試改為 assert 不附來源且含預設句關鍵字即可。

- [ ] **Step 4: 跑測試確認通過**

```bash
python -m pytest tests/unit/services/rag/test_answer_service.py -v
```

Expected: PASS（本檔既有測試全綠）

- [ ] **Step 5: Commit**

```bash
git add app/services/rag/answer_service.py tests/unit/services/rag/test_answer_service.py
git commit -m "$(cat <<'EOF'
fix(rag): 無法回答時不附加知識庫來源

EOF
)"
```

勾選 openspec tasks 1.3、1.4。

---

### Task 3: 網域白名單過濾

**Files:**
- Create: `app/services/rag/whitelist.py`
- Create: `tests/unit/services/rag/test_web_whitelist.py`
- OpenSpec: tasks 2.2、2.3

**Interfaces:**
- Consumes: 無
- Produces:
  - `ALLOWED_DOMAIN_SUFFIXES: tuple[str, ...] = ("gov.tw", "hpa.gov.tw", "cdc.gov.tw", "mohw.gov.tw")`
  - `is_allowed_url(url: str) -> bool` — hostname 等於或結尾為 `.{suffix}` 視為允許；無效 URL 回 `False`

- [ ] **Step 1: 寫失敗測試**

建立 `tests/unit/services/rag/test_web_whitelist.py`：

```python
import pytest

from app.services.rag.whitelist import is_allowed_url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.hpa.gov.tw/Pages/Detail.aspx?nodeid=1",
        "https://www.cdc.gov.tw/Category/Page/x",
        "https://www.mohw.gov.tw/cp-16-1.html",
        "https://www.gov.tw/",
        "https://health.gov.tw/news",
        "http://sub.cdc.gov.tw/path",
    ],
)
def test_is_allowed_url_accepts_whitelist_domains(url):
    assert is_allowed_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://www.google.com/search?q=高血壓",
        "https://example.com/",
        "https://gov.tw.evil.com/",
        "https://notgov.tw.example.com/",
        "not-a-url",
        "",
        "https://",
    ],
)
def test_is_allowed_url_rejects_non_whitelist(url):
    assert is_allowed_url(url) is False
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
python -m pytest tests/unit/services/rag/test_web_whitelist.py -v
```

Expected: FAIL（`ModuleNotFoundError` 或 import 失敗）

- [ ] **Step 3: 實作白名單**

建立 `app/services/rag/whitelist.py`：

```python
from urllib.parse import urlparse

ALLOWED_DOMAIN_SUFFIXES: tuple[str, ...] = (
    "gov.tw",
    "hpa.gov.tw",
    "cdc.gov.tw",
    "mohw.gov.tw",
)


def is_allowed_url(url: str) -> bool:
    raw = (url or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"}:
        return False
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return False
    for suffix in ALLOWED_DOMAIN_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return True
    return False
```

- [ ] **Step 4: 跑測試確認通過**

```bash
python -m pytest tests/unit/services/rag/test_web_whitelist.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/rag/whitelist.py tests/unit/services/rag/test_web_whitelist.py
git commit -m "$(cat <<'EOF'
feat(rag): 新增政府網域白名單 URL 過濾

EOF
)"
```

勾選 openspec tasks 2.2、2.3。

---

### Task 4: WebSearchClient Protocol 與 FirecrawlClient

**Files:**
- Create: `app/services/rag/web_client.py`
- Create: `app/services/rag/firecrawl_client.py`
- Create: `tests/unit/services/rag/test_firecrawl_client.py`
- OpenSpec: tasks 2.1、2.4

**Interfaces:**
- Consumes: 無（本 task 不依賴 whitelist；過濾在 Task 5）
- Produces:
  - `@dataclass class WebSearchHit: title: str; url: str; description: str = ""`
  - `class WebSearchClient(Protocol): async def search(self, query: str, *, limit: int = 5) -> list[WebSearchHit]: ...`；`async def scrape(self, url: str) -> str: ...`
  - `class FirecrawlClient:` 實作上述方法；constructor：
    - `api_key: str`
    - `base_url: str = "https://api.firecrawl.dev/v1"`
    - `timeout_seconds: float = 15.0`
    - `http_client: httpx.AsyncClient | None = None`（測試注入；若為 `None` 則內部建立）
  - 行為：`api_key` 空白 → `search` 回 `[]`、`scrape` 回 `""`；HTTP／解析失敗同樣回空，不拋給呼叫端

Firecrawl v1 契約（寫死於實作）：

- Search: `POST {base_url}/search`，Header `Authorization: Bearer {api_key}`，JSON `{"query": query, "limit": limit}`
- 成功 JSON：`{"success": true, "data": [{"title", "description", "url"}, ...]}`
- Scrape: `POST {base_url}/scrape`，JSON `{"url": url, "formats": ["markdown"]}`
- 成功 JSON：`{"success": true, "data": {"markdown": "..."}}`

- [ ] **Step 1: 寫失敗測試**

建立 `tests/unit/services/rag/test_firecrawl_client.py`：

```python
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.services.rag.firecrawl_client import FirecrawlClient


def _mock_response(payload: dict, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json = MagicMock(return_value=payload)
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        response.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "err",
                request=MagicMock(),
                response=MagicMock(status_code=status_code),
            )
        )
    return response


@pytest.mark.asyncio
async def test_search_parses_hits_from_firecrawl_payload():
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        return_value=_mock_response(
            {
                "success": True,
                "data": [
                    {
                        "title": "高血壓",
                        "description": "說明",
                        "url": "https://www.hpa.gov.tw/a",
                    }
                ],
            }
        )
    )
    client = FirecrawlClient(api_key="fc-test", http_client=http_client)
    hits = await client.search("高血壓", limit=3)
    assert len(hits) == 1
    assert hits[0].title == "高血壓"
    assert hits[0].url == "https://www.hpa.gov.tw/a"
    http_client.post.assert_awaited_once()
    args, kwargs = http_client.post.await_args
    assert args[0].endswith("/search")
    assert kwargs["headers"]["Authorization"] == "Bearer fc-test"
    assert kwargs["json"] == {"query": "高血壓", "limit": 3}


@pytest.mark.asyncio
async def test_scrape_returns_markdown_text():
    http_client = AsyncMock()
    http_client.post = AsyncMock(
        return_value=_mock_response(
            {"success": True, "data": {"markdown": "# 標題\n內容"}}
        )
    )
    client = FirecrawlClient(api_key="fc-test", http_client=http_client)
    text = await client.scrape("https://www.hpa.gov.tw/a")
    assert "內容" in text
    args, kwargs = http_client.post.await_args
    assert args[0].endswith("/scrape")
    assert kwargs["json"]["url"] == "https://www.hpa.gov.tw/a"
    assert "markdown" in kwargs["json"]["formats"]


@pytest.mark.asyncio
async def test_search_returns_empty_when_api_key_missing():
    http_client = AsyncMock()
    client = FirecrawlClient(api_key="", http_client=http_client)
    assert await client.search("q") == []
    http_client.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_returns_empty_on_http_error():
    http_client = AsyncMock()
    http_client.post = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
    client = FirecrawlClient(api_key="fc-test", http_client=http_client)
    assert await client.search("q") == []


@pytest.mark.asyncio
async def test_scrape_returns_empty_on_http_error():
    http_client = AsyncMock()
    http_client.post = AsyncMock(side_effect=httpx.ConnectError("down"))
    client = FirecrawlClient(api_key="fc-test", http_client=http_client)
    assert await client.scrape("https://www.hpa.gov.tw/a") == ""
```

- [ ] **Step 2: 跑測試確認失敗**

```bash
python -m pytest tests/unit/services/rag/test_firecrawl_client.py -v
```

Expected: FAIL（模組不存在）

- [ ] **Step 3: 實作 Protocol 與 FirecrawlClient**

`app/services/rag/web_client.py`：

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class WebSearchHit:
    title: str
    url: str
    description: str = ""


class WebSearchClient(Protocol):
    async def search(self, query: str, *, limit: int = 5) -> list[WebSearchHit]: ...

    async def scrape(self, url: str) -> str: ...
```

`app/services/rag/firecrawl_client.py`：

```python
from __future__ import annotations

import logging

import httpx

from app.services.rag.web_client import WebSearchHit

logger = logging.getLogger(__name__)


class FirecrawlClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.firecrawl.dev/v1",
        timeout_seconds: float = 15.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._http_client = http_client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def search(self, query: str, *, limit: int = 5) -> list[WebSearchHit]:
        if not self._api_key:
            return []
        client = self._http_client or httpx.AsyncClient(timeout=self._timeout_seconds)
        owns_client = self._http_client is None
        try:
            response = await client.post(
                f"{self._base_url}/search",
                headers=self._headers(),
                json={"query": query, "limit": limit},
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            logger.exception("Firecrawl search failed")
            return []
        finally:
            if owns_client:
                await client.aclose()

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return []
        hits: list[WebSearchHit] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            hits.append(
                WebSearchHit(
                    title=str(item.get("title") or "").strip(),
                    url=url,
                    description=str(item.get("description") or "").strip(),
                )
            )
        return hits

    async def scrape(self, url: str) -> str:
        if not self._api_key:
            return ""
        client = self._http_client or httpx.AsyncClient(timeout=self._timeout_seconds)
        owns_client = self._http_client is None
        try:
            response = await client.post(
                f"{self._base_url}/scrape",
                headers=self._headers(),
                json={"url": url, "formats": ["markdown"]},
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            logger.exception("Firecrawl scrape failed")
            return ""
        finally:
            if owns_client:
                await client.aclose()

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return ""
        return str(data.get("markdown") or "").strip()
```

- [ ] **Step 4: 跑測試確認通過**

```bash
python -m pytest tests/unit/services/rag/test_firecrawl_client.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/rag/web_client.py app/services/rag/firecrawl_client.py tests/unit/services/rag/test_firecrawl_client.py
git commit -m "$(cat <<'EOF'
feat(rag): 新增可注入的 Firecrawl WebSearchClient

EOF
)"
```

勾選 openspec tasks 2.1、2.4。

---

### Task 5: RagAnswerService 串接 Web Fallback

**Files:**
- Modify: `app/services/rag/answer_service.py`
- Modify: `app/services/rag/__init__.py`（匯出 `NO_ANSWER_MESSAGE` 若測試需要）
- Test: `tests/unit/services/rag/test_answer_service.py`
- OpenSpec: tasks 3.1、3.2、3.3

**Interfaces:**
- Consumes: `WebSearchClient`（Task 4）、`is_allowed_url`（Task 3）、`_is_cannot_answer`／`_append_sources`（Task 1–2）
- Produces: 擴充後的 `RagAnswerService.__init__(gemini_service, retriever, web_client: WebSearchClient | None = None)` 與完整 `answer()` 流程：

```text
docs = retrieve(query)
if docs:
  kb_answer = generate_from_kb(docs)
  if not cannot_answer(kb_answer):
    return cite(kb_answer, docs, kind=kb)
  # 無法回答：不附 KB 來源，落入 web
web_docs = web_search_and_fetch(query)  # 白名單內 ≤3
if not web_docs:
  return NO_ANSWER_MESSAGE  # 不附來源
web_answer = generate_from_web(web_docs)
return "以下參考網路公開資料\n\n" + cite(web_answer, web_docs, kind=web)
```

- [ ] **Step 1: 更新 `_make_service` 並寫失敗測試**

將 helper 改為可注入 `web_client`：

```python
def _make_service(*, docs, answer_content="RAG 回覆", web_client=None):
    gemini_service = MagicMock()
    gemini_service.chat_model = MagicMock()
    gemini_service.chat_model.ainvoke = AsyncMock(
        return_value=AIMessage(content=answer_content)
    )
    retriever = MagicMock()
    retriever.ainvoke = AsyncMock(return_value=docs)
    return (
        RagAnswerService(
            gemini_service=gemini_service,
            retriever=retriever,
            web_client=web_client,
        ),
        gemini_service,
        retriever,
    )
```

新增 fake web client（同一測試檔內即可）：

```python
from app.services.rag.web_client import WebSearchHit


class FakeWebClient:
    def __init__(self, hits=None, pages=None, search_error=None, scrape_error=None):
        self.hits = hits or []
        self.pages = pages or {}
        self.search_error = search_error
        self.scrape_error = scrape_error
        self.search_calls: list[str] = []
        self.scrape_calls: list[str] = []

    async def search(self, query: str, *, limit: int = 5):
        self.search_calls.append(query)
        if self.search_error:
            raise self.search_error
        return self.hits[:limit]

    async def scrape(self, url: str) -> str:
        self.scrape_calls.append(url)
        if self.scrape_error:
            raise self.scrape_error
        return self.pages.get(url, "")
```

測試案例：

```python
from app.services.rag import NO_ANSWER_MESSAGE  # 或自 answer_service import


@pytest.mark.asyncio
async def test_answer_uses_web_fallback_when_no_kb_docs():
    web = FakeWebClient(
        hits=[
            WebSearchHit(
                title="國健署高血壓",
                url="https://www.hpa.gov.tw/htn",
            ),
            WebSearchHit(title="論壇", url="https://forum.example/htn"),
        ],
        pages={
            "https://www.hpa.gov.tw/htn": "控制血壓要規律量測與低鈉飲食。"
        },
    )
    svc, gemini, _retriever = _make_service(
        docs=[],
        answer_content="根據網路資料，請規律量測血壓。",
        web_client=web,
    )
    result = await svc.answer("高血壓要注意什麼")
    assert "以下參考網路公開資料" in result
    assert "根據網路資料，請規律量測血壓。" in result
    assert "[1] 網路：國健署高血壓：https://www.hpa.gov.tw/htn" in result
    assert "forum.example" not in result
    assert web.search_calls == ["高血壓要注意什麼"]
    gemini.chat_model.ainvoke.assert_awaited()


@pytest.mark.asyncio
async def test_answer_uses_web_when_kb_cannot_answer():
    kb_docs = [
        Document(
            page_content="無關",
            metadata={"source_name": "KB", "url": "https://www.hpa.gov.tw/kb"},
        )
    ]
    web = FakeWebClient(
        hits=[WebSearchHit(title="疾管署", url="https://www.cdc.gov.tw/w")],
        pages={"https://www.cdc.gov.tw/w": "流感疫苗建議。"},
    )
    gemini_service = MagicMock()
    gemini_service.chat_model = MagicMock()
    gemini_service.chat_model.ainvoke = AsyncMock(
        side_effect=[
            AIMessage(content="我不知道這個問題的答案。"),
            AIMessage(content="建議依時程接種流感疫苗。"),
        ]
    )
    retriever = MagicMock()
    retriever.ainvoke = AsyncMock(return_value=kb_docs)
    svc = RagAnswerService(
        gemini_service=gemini_service,
        retriever=retriever,
        web_client=web,
    )
    result = await svc.answer("流感疫苗")
    assert "以下參考網路公開資料" in result
    assert "建議依時程接種流感疫苗。" in result
    assert "https://www.hpa.gov.tw/kb" not in result
    assert "[1] 網路：疾管署：https://www.cdc.gov.tw/w" in result


@pytest.mark.asyncio
async def test_answer_returns_no_answer_without_sources_when_web_fails():
    web = FakeWebClient(hits=[], pages={})
    svc, gemini, _ = _make_service(docs=[], web_client=web)
    result = await svc.answer("完全查不到的問題")
    assert result == NO_ANSWER_MESSAGE
    assert "參考資料來源" not in result
    gemini.chat_model.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_answer_degrades_when_web_client_raises():
    web = FakeWebClient(search_error=RuntimeError("boom"))
    svc, _, _ = _make_service(docs=[], web_client=web)
    result = await svc.answer("問題")
    assert result == NO_ANSWER_MESSAGE
    assert "參考資料來源" not in result


@pytest.mark.asyncio
async def test_answer_does_not_mix_kb_and_web_sources():
    kb_docs = [
        Document(
            page_content="KB 內容",
            metadata={"source_name": "KB來源", "url": "https://www.hpa.gov.tw/kb"},
        )
    ]
    web = FakeWebClient(
        hits=[WebSearchHit(title="Web", url="https://www.mohw.gov.tw/w")],
        pages={"https://www.mohw.gov.tw/w": "Web 內容"},
    )
    gemini_service = MagicMock()
    gemini_service.chat_model = MagicMock()
    gemini_service.chat_model.ainvoke = AsyncMock(
        side_effect=[
            AIMessage(content="找不到相關資訊。"),
            AIMessage(content="這是網路答案。"),
        ]
    )
    retriever = MagicMock()
    retriever.ainvoke = AsyncMock(return_value=kb_docs)
    svc = RagAnswerService(
        gemini_service=gemini_service,
        retriever=retriever,
        web_client=web,
    )
    result = await svc.answer("混合測試")
    assert "KB來源" not in result
    assert "https://www.hpa.gov.tw/kb" not in result
    assert "網路：Web：https://www.mohw.gov.tw/w" in result
```

**必須更新既有測試** `test_answer_returns_message_when_no_docs`：無 docs 且無 web_client／web 失敗時，改為期望 `NO_ANSWER_MESSAGE`（不再是 `NO_HITS_MESSAGE`）。可保留 `NO_HITS_MESSAGE` 常數以相容匯出，但 `answer()` 雙失敗路徑應回 `NO_ANSWER_MESSAGE`。

- [ ] **Step 2: 跑新測試確認失敗**

```bash
python -m pytest tests/unit/services/rag/test_answer_service.py::test_answer_uses_web_fallback_when_no_kb_docs tests/unit/services/rag/test_answer_service.py::test_answer_uses_web_when_kb_cannot_answer tests/unit/services/rag/test_answer_service.py::test_answer_returns_no_answer_without_sources_when_web_fails tests/unit/services/rag/test_answer_service.py::test_answer_degrades_when_web_client_raises tests/unit/services/rag/test_answer_service.py::test_answer_does_not_mix_kb_and_web_sources -v
```

Expected: FAIL

- [ ] **Step 3: 實作 orchestration**

更新 `RagAnswerService`：

```python
from app.services.rag.whitelist import is_allowed_url
from app.services.rag.web_client import WebSearchClient

WEB_ANSWER_PREFIX = "以下參考網路公開資料"
WEB_SEARCH_LIMIT = 8
WEB_PAGE_CHAR_LIMIT = 8000

# 可沿用既有 RAG_PROMPT；web 路徑同樣用該 prompt（context 改為網頁正文）
```

```python
class RagAnswerService:
    def __init__(
        self,
        gemini_service: GeminiService,
        retriever: MongoAtlasVectorRetriever,
        web_client: WebSearchClient | None = None,
    ) -> None:
        self.gemini_service = gemini_service
        self.retriever = retriever
        self.web_client = web_client

    async def answer(self, user_text: str) -> str:
        docs = await self.retriever.ainvoke(user_text)
        if docs:
            kb_answer = await self._generate_answer(user_text, docs)
            if not self._is_cannot_answer(kb_answer):
                return self._append_sources(kb_answer, docs, source_kind="kb")

        web_docs = await self._fetch_web_docs(user_text)
        if not web_docs:
            return NO_ANSWER_MESSAGE

        web_answer = await self._generate_answer(user_text, web_docs)
        if self._is_cannot_answer(web_answer):
            return NO_ANSWER_MESSAGE

        annotated = f"{WEB_ANSWER_PREFIX}\n\n{web_answer}"
        return self._append_sources(annotated, web_docs, source_kind="web")

    async def _generate_answer(self, question: str, docs: list[Document]) -> str:
        context = "\n".join(
            f"{idx}. {doc.page_content}" for idx, doc in enumerate(docs, start=1)
        )
        messages = RAG_PROMPT.format_messages(question=question, context=context)
        rag_result = await self.gemini_service.chat_model.ainvoke(messages)
        answer_text = rag_result.content or "抱歉，我目前找不到相關資料，請稍後再試。"
        if not isinstance(answer_text, str):
            answer_text = str(answer_text)
        return answer_text

    async def _fetch_web_docs(self, query: str) -> list[Document]:
        if self.web_client is None:
            return []
        try:
            hits = await self.web_client.search(query, limit=WEB_SEARCH_LIMIT)
        except Exception:
            return []

        docs: list[Document] = []
        seen: set[str] = set()
        for hit in hits:
            url = (hit.url or "").strip()
            if not url or url in seen or not is_allowed_url(url):
                continue
            try:
                text = await self.web_client.scrape(url)
            except Exception:
                continue
            text = (text or "").strip()
            if not text:
                continue
            seen.add(url)
            docs.append(
                Document(
                    page_content=text[:WEB_PAGE_CHAR_LIMIT],
                    metadata={
                        "source_name": (hit.title or "").strip() or url,
                        "url": url,
                    },
                )
            )
            if len(docs) >= CITE_TOP_K:
                break
        return docs
```

更新 `__init__.py` 匯出 `NO_ANSWER_MESSAGE`（若測試從 package import）。

- [ ] **Step 4: 跑 RAG answer 測試全檔**

```bash
python -m pytest tests/unit/services/rag/test_answer_service.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/rag/answer_service.py app/services/rag/__init__.py tests/unit/services/rag/test_answer_service.py
git commit -m "$(cat <<'EOF'
feat(rag): KB 不足時啟用 Web Fallback 並標註網路來源

EOF
)"
```

勾選 openspec tasks 3.1、3.2、3.3。

---

### Task 6: DI 組裝與環境設定

**Files:**
- Modify: `app/core/config.py`
- Modify: `app/dependencies.py`
- Modify: `.env.example`
- OpenSpec: task 3.4

**Interfaces:**
- Consumes: `FirecrawlClient`、`RagAnswerService(web_client=...)`
- Produces: 執行期組裝；`settings.FIRECRAWL_API_KEY`；無金鑰時 `web_client=None`（降級為無法回答）

此 task 以靜態檢查＋既有單元測試為主（不打真實 Firecrawl）。若專案尚無針對 `dependencies` 的單元測試，**不要**為 DI 新增 monkey patch 測試；改以手動核對 import／建構參數，並跑全套 pytest 確保未破壞匯入。

- [ ] **Step 1: 擴充 Settings 與 `.env.example`**

`app/core/config.py` 新增：

```python
# Firecrawl（RAG Web Fallback）
FIRECRAWL_API_KEY: str = os.getenv("FIRECRAWL_API_KEY", "")
```

`.env.example` 新增區塊：

```bash
# Firecrawl（RAG Web Fallback；未設定則網路補齊降級為無法回答）
FIRECRAWL_API_KEY=your_firecrawl_api_key_here
```

- [ ] **Step 2: 於 `dependencies.py` 組裝**

在 RAG 區塊改為：

```python
from app.services.rag import MongoAtlasVectorRetriever, RETRIEVAL_TOP_K, RagAnswerService
from app.services.rag.firecrawl_client import FirecrawlClient

_firecrawl_client = None
if settings.FIRECRAWL_API_KEY:
    _firecrawl_client = FirecrawlClient(api_key=settings.FIRECRAWL_API_KEY)

_rag_answer_service = RagAnswerService(
    gemini_service=_gemini_service,
    retriever=_rag_retriever,
    web_client=_firecrawl_client,
)
```

- [ ] **Step 3: 確認模組可 import（無真實網路）**

```bash
python -c "from app.services.rag.firecrawl_client import FirecrawlClient; from app.services.rag.answer_service import RagAnswerService; print('ok')"
```

Expected: 印出 `ok`

- [ ] **Step 4: 跑 rag 單元測試**

```bash
python -m pytest tests/unit/services/rag/ -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/core/config.py app/dependencies.py .env.example
git commit -m "$(cat <<'EOF'
chore(rag): 組裝 Firecrawl client 並補齊環境變數說明

EOF
)"
```

勾選 openspec task 3.4。

---

### Task 7: 全量驗收與 OpenSpec 勾核

**Files:**
- Verify only（必要時微調測試斷言／文案常數）
- OpenSpec: tasks 4.1、4.2、4.3

**Interfaces:**
- Consumes: Task 1–6 全部交付物
- Produces: 全綠 pytest、tasks.md 全勾、清楚 commit（若尚有未提交修正）

- [ ] **Step 1: 對照 spec 情景勾核（測試或手動清單）**

對照 `openspec/changes/rag-cite-and-web-fallback/specs/rag-responses/spec.md`，確認下列皆有對應測試通過：

| Spec 情景 | 對應測試 |
| --- | --- |
| 跳過無 URL 後編號連續 | `test_append_sources_renumbers_after_skipping_missing_and_duplicate_urls` |
| 跳過重複 URL 後編號連續 | 同上 |
| 內容不足不附 KB 來源 | `test_answer_omits_kb_sources_when_model_cannot_answer` |
| 知識庫無命中改走 Web | `test_answer_uses_web_fallback_when_no_kb_docs` |
| 內容不足後改走 Web | `test_answer_uses_web_when_kb_cannot_answer` |
| 非白名單網域被過濾 | `test_web_whitelist` + web fallback 測試不含 forum URL |
| KB 與 Web 皆失敗 | `test_answer_returns_no_answer_without_sources_when_web_fails` |
| 同一答不混 KB／Web | `test_answer_does_not_mix_kb_and_web_sources` |
| 服務未初始化 | 既有 `get_rag_answer` 行為（`rag_tools.py`）不變，無需改碼 |

- [ ] **Step 2: 跑全量測試**

```bash
./init.sh
```

或等價：

```bash
python -m pytest tests/ -v
```

Expected: 全綠

- [ ] **Step 3: 勾選 openspec tasks 4.1–4.3，並確認 `tasks.md` 全部 `[x]`**

- [ ] **Step 4: 若 Step 2 有小修正，另開 commit**

```bash
git add -A
git status
git commit -m "$(cat <<'EOF'
test(rag): 完成 cite 與 web fallback 驗收對齊

EOF
)"
```

（僅在有未提交變更時執行；無變更則跳過）

---

## Self-Review Checklist

| Spec 要求 | Plan 對應 |
| --- | --- |
| 參考來源連續編號 | Task 1 |
| 無法回答不附 KB 來源 | Task 2 |
| Web Fallback（空 docs／無法回答） | Task 5 |
| 白名單網域 | Task 3（過濾）+ Task 5（套用） |
| 網路來源標註 | Task 1（格式）+ Task 5（前綴句） |
| 最多 3 筆、不混用 | Task 5 |
| 雙失敗不附來源 | Task 5 |
| 無 feature flag、DI、禁 monkey patch | Task 4–6 |
| Firecrawl 金鑰設定 | Task 6 |
| `get_rag_answer` 工具面不變 | 全計劃未改 `rag_tools.py` 對外簽名 |

Placeholder scan：無 TBD／TODO／「之後實作」步驟；測試皆含完整程式碼與確切指令。

Type consistency：`WebSearchClient.search/scrape`、`WebSearchHit`、`source_kind`、`NO_ANSWER_MESSAGE`、`FirecrawlClient(http_client=...)` 於各 task 名稱一致。
