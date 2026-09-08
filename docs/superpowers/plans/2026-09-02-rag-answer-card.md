# RAG 回覆卡片化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 `get_rag_answer` 與 `answer_from_uploaded_document` 的回覆以 LINE Flex 卡片送出，使字級跟隨使用者在 LIFF 設定的 `UserSettings.font_size`。

**Architecture:** 卡片在**呈現層**（`reply.py`）組裝，不在 LangChain tool 內組裝。agent 照常產出純文字、純文字存進對話歷史，`reply()` 收到 `answer_kind` 才把文字組成卡片；組不成或太大就原樣送純文字。結構化來源透過 request-scoped ContextVar 從 `RagAnswerService` 傳到呈現層，因為 LangChain tool 的回傳型別只能是字串。

**Tech Stack:** Python 3.13、FastAPI、LangGraph、`linebot.v3.messaging`、pytest。

## Global Constraints

- 規格來源：`openspec/changes/rag-answer-card/`（proposal.md / design.md / specs/）。有疑義時以該處為準。
- LINE 回覆中**模型產出的文字**一律不得含 Markdown（`openspec/specs/line-reply-rules`）。卡片內文字同樣適用。
- 卡片所有 size 一律取自 `resolve_theme()`，**禁止寫死** `"lg"`／`"xxl"` 這類 keyword。
- LINE 單一 bubble JSON 上限 **10 KB**；本專案安全門檻 **9 KB**（`SAFE_BUBBLE_BYTES`）。
- 量測大小一律用 `json.dumps(x).encode()`（預設 `ensure_ascii=True`），與 `linebot/v3/messaging/rest.py:155` 實際送出的序列化一致。**不可**用 `ensure_ascii=False` 計算——中文字實際佔 6 bytes 不是 3 bytes。
- 測試禁止用 monkey patch 注入**應用層依賴**（`openspec/config.yaml`）；一律用建構子注入 fake。**例外**：patch LINE SDK 的模組邊界（`Configuration`／`ApiClient`／`MessagingApi`）是本 repo 既有且唯一可行的做法，見 `tests/unit/services/line_messaging/test_reply.py::_send_reply`，沿用它。
- 每個 Task 結束前跑該 Task 的測試，全綠才 commit。全部做完跑 `./init.sh`。
- Commit 訊息用繁體中文，格式比照 `git log`（`feat(scope): …`／`fix(scope): …`）。

## File Structure

**新增**

| 檔案 | 職責 |
|---|---|
| `resources/flex_messages/size_guard.py` | 量測 bubble 上線位元組、判定是否可送出。純函式，無狀態。 |
| `app/core/rag_sources.py` | 結構化來源的 request-scoped ContextVar，形狀比照 `app/core/user_font_size.py`。 |
| `app/services/line_messaging/flex/rag_answer_flex.py` | 兩個卡片 builder（衛教問答、文件問答）。只組裝，不決定降級。 |

**修改**

| 檔案 | 改什麼 |
|---|---|
| `app/tools/claim_tools.py` | 判定卡送出前過大小防線 |
| `app/i18n/messages.py` | 新增 `all_rag_prefixes()` / `strip_rag_prefix()` |
| `app/services/rag/answer_service.py` | `_append_sources` 一併產出 `SourceRef` |
| `app/services/rag/answer_prompts.py` | 三個生成 prompt 加字數上限 |
| `app/services/agent/agent.py` | `invoke()` 回傳新增 `answer_kind` |
| `app/services/line_messaging/reply/reply.py` | `reply()` 新增 `answer_kind`，卡片分支＋降級＋語音 |
| `app/services/line_messaging/handler/message_handler.py` | 傳遞 `answer_kind`、管理來源 ContextVar 生命週期 |

**依賴順序：** Task 1 → 2 可先獨立完成並上線（擋既有風險）。Task 3 → 4 → 5 → 6 → 7 → 8 是主線。Task 9 獨立。

---

### Task 1: Flex bubble 大小防線

**Files:**
- Create: `resources/flex_messages/size_guard.py`
- Test: `tests/unit/flex_messages/test_size_guard.py`

**Interfaces:**
- Consumes: 無
- Produces: `wire_bytes(bubble: dict) -> int`、`fits(bubble: dict, limit: int = SAFE_BUBBLE_BYTES) -> bool`、常數 `LINE_BUBBLE_LIMIT_BYTES = 10240`、`SAFE_BUBBLE_BYTES = 9216`

- [ ] **Step 1: 寫失敗測試**

```python
# tests/unit/flex_messages/test_size_guard.py
import json

from resources.flex_messages.size_guard import (
    LINE_BUBBLE_LIMIT_BYTES,
    SAFE_BUBBLE_BYTES,
    fits,
    wire_bytes,
)


def _bubble(text: str) -> dict:
    return {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [{"type": "text", "text": text, "wrap": True}],
        },
    }


def test_wire_bytes_counts_non_ascii_as_escaped():
    """中文在上線時是 \\uXXXX（6 bytes），不是 UTF-8 的 3 bytes。

    這是本模組存在的理由：linebot/v3/messaging/rest.py:155 用
    json.dumps(body) 的預設 ensure_ascii=True 送出。若改用未轉義的
    UTF-8 計算，會低估一倍，防線等於失效。
    """
    one_char = _bubble("衛")
    ten_chars = _bubble("衛" * 10)

    assert wire_bytes(ten_chars) - wire_bytes(one_char) == 9 * 6
    assert wire_bytes(ten_chars) > len(json.dumps(ten_chars, ensure_ascii=False).encode())


def test_safe_threshold_leaves_headroom_below_line_limit():
    assert SAFE_BUBBLE_BYTES < LINE_BUBBLE_LIMIT_BYTES
    assert LINE_BUBBLE_LIMIT_BYTES == 10 * 1024


def test_fits_accepts_small_bubble():
    assert fits(_bubble("蜂蜜不需要放冰箱。")) is True


def test_fits_rejects_oversized_bubble():
    assert fits(_bubble("衛" * 3000)) is False


def test_fits_honours_explicit_limit():
    bubble = _bubble("衛" * 100)
    assert fits(bubble, limit=wire_bytes(bubble)) is True
    assert fits(bubble, limit=wire_bytes(bubble) - 1) is False
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/flex_messages/test_size_guard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'resources.flex_messages.size_guard'`

- [ ] **Step 3: 寫實作**

```python
# resources/flex_messages/size_guard.py
"""Flex bubble 送出前的大小防線。

LINE 對單一 bubble 的 JSON 有 10 KB 上限，超過會在 reply_message() 被以
400 拒收。那個例外會被 reply.py 的 except 接住，使用者最後什麼都收不到——
比退回純文字糟得多，因此要在送出前先擋下來。

量測必須與實際送出的序列化一致：linebot/v3/messaging/rest.py:155 是
`json.dumps(body)`，用預設的 ensure_ascii=True，因此每個中文字在傳輸時是
`\\uXXXX` 共 6 bytes，不是 UTF-8 的 3 bytes。用 ensure_ascii=False 計算會
低估一倍，等於沒有防線。
"""

from __future__ import annotations

import json
from typing import Any

# LINE 官方對單一 bubble 的 JSON 大小上限。
LINE_BUBBLE_LIMIT_BYTES = 10 * 1024

# 本專案的門檻，比官方上限低約 10%。留餘裕的理由：LINE 側是否以完全相同的
# 方式計算大小並未見於文件，貼著上限送等於把「使用者收不到回覆」的風險押在
# 一個沒有保證的假設上。
SAFE_BUBBLE_BYTES = 9 * 1024


def wire_bytes(bubble: dict[str, Any]) -> int:
    """回傳這個 bubble 實際送出時佔用的位元組數。"""
    return len(json.dumps(bubble).encode("utf-8"))


def fits(bubble: dict[str, Any], limit: int = SAFE_BUBBLE_BYTES) -> bool:
    """這個 bubble 是否可安全送出。"""
    return wire_bytes(bubble) <= limit
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/flex_messages/test_size_guard.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit**

```bash
git add resources/flex_messages/size_guard.py tests/unit/flex_messages/test_size_guard.py
git commit -m "feat(flex): 新增 bubble 大小防線，以上線位元組量測"
```

---

### Task 2: 判定卡接上大小防線

**Files:**
- Modify: `app/tools/claim_tools.py`（`_to_flex_message_text` 與 `verify_claim`）
- Test: `tests/unit/tools/test_claim_tools.py`（既有檔案，追加）

**Interfaces:**
- Consumes: Task 1 的 `fits`
- Produces: 無新介面。`_to_flex_message_text` 的回傳型別由 `str` 改為 `str | None`（`None` = 超過門檻）

**Why this task:** 這是**目前已上線**的風險，與卡片化功能無關但用同一道防線。實測一則 `related_info` 1,136 字的真實判定卡為 8,110 bytes，已達上限 79%；再多一篇衛教文章就會被 LINE 拒收，而使用者會完全收不到回覆。

- [ ] **Step 1: 寫失敗測試**

```python
# 追加到 tests/unit/tools/test_claim_tools.py
import json

import pytest

from app.services.rag.claim_verification.service import VerificationResult
from app.tools import claim_tools


class FakeClaimVerificationService:
    """建構子注入用的 fake；回傳固定的 VerificationResult。"""

    def __init__(self, result: VerificationResult):
        self._result = result

    async def verify(self, user_text: str) -> VerificationResult:
        return self._result


def _result(related_info: str) -> VerificationResult:
    return VerificationResult(
        user_question="網傳蜂蜜可以抗癌",
        verdict="證據不足",
        reasoning="台灣事實查核中心目前沒有針對這則說法的查核報告。",
        source_title="",
        source_url="",
        matched=False,
        related_info=related_info,
        verdict_slug="not-enough-evidence",
    )


@pytest.mark.asyncio
async def test_oversized_verdict_card_falls_back_to_text():
    """related_info 沒有長度上限，塞爆時必須退回純文字而不是送出被拒收的卡片。"""
    claim_tools.configure_claim_tool(
        FakeClaimVerificationService(_result("衛" * 3000))
    )

    reply = await claim_tools.verify_claim.ainvoke({"query": "網傳蜂蜜可以抗癌"})

    assert not reply.strip().startswith("{")
    assert "判定：證據不足" in reply
    assert "衛衛衛" in reply


@pytest.mark.asyncio
async def test_normal_verdict_card_stays_flex():
    """防線不得誤殺正常大小的卡片。"""
    claim_tools.configure_claim_tool(
        FakeClaimVerificationService(_result("蜂蜜不需要放冰箱，室溫避光即可。"))
    )

    reply = await claim_tools.verify_claim.ainvoke({"query": "網傳蜂蜜可以抗癌"})

    payload = json.loads(reply)
    assert payload["type"] == "flex"
    assert payload["contents"]["type"] == "bubble"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/tools/test_claim_tools.py -k "oversized or stays_flex" -v`
Expected: `test_oversized_verdict_card_falls_back_to_text` FAIL — 目前會回傳 Flex JSON 字串，`reply.strip().startswith("{")` 為真。另一個測試 PASS。

- [ ] **Step 3: 寫實作**

在 `app/tools/claim_tools.py` 的 import 區加入：

```python
from resources.flex_messages.size_guard import fits
```

把 `_to_flex_message_text` 改為：

```python
def _to_flex_message_text(result: VerificationResult) -> str | None:
    """把判定卡組成 LINE Flex Message JSON 字串；超過大小門檻時回傳 None。

    格式比照 `official_site_tools.open_official_site`：
    `{"type": "flex", "altText": ..., "contents": {...}}`，這是
    `reply.py._try_parse_flex_message` 認得、會還原成真正 FlexMessage 送出的
    形狀。`app/services/agent/agent.py` 的 `medical_tool_names` 另外會把這個
    字串直接當成最終回覆、跳過模型再次改寫（見該處註解），因此這裡的輸出
    格式必須與其他 Flex 工具一致，不能只是「看起來像 JSON」。

    大小檢查在這裡而非 `build_verdict_flex` 裡：退回純文字的決策點在本模組
    （`_format_verdict_reply` 已是既有的 fallback），builder 維持只負責組裝
    的單一職責。回傳 None 而非拋例外，是為了讓「太大」與「組裝壞掉」在
    `verify_claim` 裡分別留下不同的 log——兩者都退回純文字，但成因不同。
    """
    flex_message = build_verdict_flex(result)
    payload = flex_message.to_dict()
    if not fits(payload["contents"]):
        return None
    return json.dumps(payload, ensure_ascii=False)
```

把 `verify_claim` 的結尾改為：

```python
    result = await _claim_verification_service.verify(query)
    try:
        flex_text = _to_flex_message_text(result)
    except Exception:  # noqa: BLE001
        # Flex 組裝是呈現層的最後一步，任何非預期例外都不該讓使用者拿到堆疊
        # 追蹤或空白回覆；退回 Flex 化之前就存在的純文字格式，判定內容仍能
        # 送到使用者手上。
        logger.warning("判定卡 Flex 組裝失敗，改回純文字格式", exc_info=True)
        return _format_verdict_reply(result)

    if flex_text is None:
        # 超過 LINE 的 bubble 上限。硬送出去會在 reply_message() 被以 400
        # 拒收，例外被 reply() 的 except 吞掉後使用者什麼都收不到，比純文字
        # 糟得多。未命中時 related_info 放的是衛教文章全文，沒有長度上限。
        logger.warning(
            "判定卡超過 Flex 大小上限，改回純文字格式，matched=%s", result.matched
        )
        return _format_verdict_reply(result)

    return flex_text
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/tools/test_claim_tools.py -v`
Expected: PASS（含既有測試全綠）

- [ ] **Step 5: Commit**

```bash
git add app/tools/claim_tools.py tests/unit/tools/test_claim_tools.py
git commit -m "fix(claim): 判定卡超過 LINE 大小上限時退回純文字

未命中時 related_info 放的是衛教文章全文，沒有長度上限。實測一則
related_info 1,136 字的真實卡片為 8,110 bytes，已達 10 KB 上限的 79%。
超過時 build_verdict_flex 不會拋例外，既有的組裝失敗 fallback 因此不會
觸發，訊息會在 reply_message() 被 LINE 以 400 拒收，例外被 reply() 的
except 接住後只留一行 log，使用者完全收不到回覆。"
```

---

### Task 3: 結構化來源（ContextVar + answer_service）

**Files:**
- Create: `app/core/rag_sources.py`
- Modify: `app/services/rag/answer_service.py`（`_append_sources`，約 270-313 行）
- Test: `tests/unit/core/test_rag_sources.py`、`tests/unit/services/rag/test_answer_service.py`（既有檔案，追加）

**Interfaces:**
- Consumes: 無
- Produces: `SourceRef(index: int, label: str, url: str)`（frozen dataclass）、`get_request_rag_sources() -> tuple[SourceRef, ...]`、`set_request_rag_sources(sources) -> Token`、`reset_request_rag_sources(token) -> None`

**Why a ContextVar:** `get_rag_answer` 是 LangChain tool，回傳型別只能是字串，結構化資料沒有別的路徑傳到呈現層。這與 `app/core/user_font_size.py`、`app/core/user_language.py` 是同一套模式、同一個理由。

**Why not 反解字串:** 文字清單長這樣 `[1] 食藥署：https://...`，分隔符是全形冒號，而來源名本身也可能含冒號。`_append_sources` 內部本來就同時握有重編號後的編號與對應的 `Document`，直接取 metadata 比反解可靠。

- [ ] **Step 1: 寫 ContextVar 的失敗測試**

```python
# tests/unit/core/test_rag_sources.py
from app.core.rag_sources import (
    SourceRef,
    get_request_rag_sources,
    reset_request_rag_sources,
    set_request_rag_sources,
)


def test_default_is_empty():
    assert get_request_rag_sources() == ()


def test_set_and_reset_round_trip():
    refs = (SourceRef(index=1, label="食藥署", url="https://www.fda.gov.tw/x"),)

    token = set_request_rag_sources(refs)
    try:
        assert get_request_rag_sources() == refs
    finally:
        reset_request_rag_sources(token)

    assert get_request_rag_sources() == ()


def test_set_coerces_to_tuple():
    """存進去的必須是不可變序列，避免呼叫端事後改到已設定的值。"""
    token = set_request_rag_sources(
        [SourceRef(index=1, label="台灣 e 院", url="https://sp1.hso.mohw.gov.tw/x")]
    )
    try:
        assert isinstance(get_request_rag_sources(), tuple)
    finally:
        reset_request_rag_sources(token)


def test_source_ref_is_frozen():
    ref = SourceRef(index=1, label="食藥署", url="https://www.fda.gov.tw/x")
    try:
        ref.index = 2  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("SourceRef 應為 frozen dataclass")
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/core/test_rag_sources.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.rag_sources'`

- [ ] **Step 3: 寫 ContextVar 實作**

```python
# app/core/rag_sources.py
"""本輪 RAG 回答的結構化參考來源，request-scoped ContextVar。

與 user_language、user_font_size 採同一套模式，理由也相同：`get_rag_answer`
是 LangChain tool，回傳型別只能是字串，結構化資料沒有別的路徑傳到呈現層。

呈現層要把來源做成可點的 URI action 按鈕，需要 (label, url)。若改從最終
文字反解 `[1] 食藥署：https://...`，分隔符是全形冒號、而來源名本身也可能
含冒號，解析很脆；`_append_sources` 內部本來就握有編號與對應的 Document，
直接在那裡取 metadata 可靠得多。
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class SourceRef:
    """一筆參考來源。

    index 是重編號後的顯示編號，必須與文字清單中的 [n] 完全一致——答案本文
    裡的引用標記指的就是這個編號，兩者漂移會讓使用者點錯來源。

    url 可能為空字串：`rag-responses` 明文要求缺少 url 的來源仍須顯示（以
    「來源名｜標題」呈現），不得靜默丟棄。呈現層負責決定空 url 時不產生按鈕。
    """

    index: int
    label: str
    url: str


_request_rag_sources: ContextVar[tuple[SourceRef, ...]] = ContextVar(
    "care_request_rag_sources",
    default=(),
)


def get_request_rag_sources() -> tuple[SourceRef, ...]:
    return _request_rag_sources.get()


def set_request_rag_sources(sources: Iterable[SourceRef]) -> Token:
    return _request_rag_sources.set(tuple(sources))


def reset_request_rag_sources(token: Token) -> None:
    _request_rag_sources.reset(token)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/core/test_rag_sources.py -v`
Expected: PASS（4 passed）

- [ ] **Step 5: 寫 answer_service 的失敗測試**

```python
# 追加到 tests/unit/services/rag/test_answer_service.py
from langchain_core.documents import Document

from app.core.rag_sources import get_request_rag_sources
from app.services.rag.answer_service import RagAnswerService


def _doc(source_name: str, title: str, url: str) -> Document:
    return Document(
        page_content="內容",
        metadata={"source_name": source_name, "original_title": title, "url": url},
    )


def test_structured_sources_match_text_numbering():
    """結構化來源的 index 必須與文字清單的 [n] 逐筆對應。

    答案本文的引用標記指的就是這個編號；兩者各自編號會讓使用者點錯來源。
    這裡答案先引用第 2 篇再引用第 1 篇，因此重編號後 [1] 是原本的第 2 篇。
    """
    docs = [
        _doc("台灣 e 院", "蜂蜜保存", "https://sp1.hso.mohw.gov.tw/a"),
        _doc("食藥署", "蜂蜜加熱", "https://www.fda.gov.tw/b"),
    ]

    text = RagAnswerService._append_sources("加熱不會有毒 [2]。放室溫即可 [1]。", docs)

    refs = get_request_rag_sources()
    assert [r.index for r in refs] == [1, 2]
    assert [r.label for r in refs] == ["食藥署", "台灣 e 院"]
    assert [r.url for r in refs] == [
        "https://www.fda.gov.tw/b",
        "https://sp1.hso.mohw.gov.tw/a",
    ]
    assert "[1] 食藥署" in text
    assert "[2] 台灣 e 院" in text


def test_structured_sources_empty_when_no_citation():
    """模型沒輸出任何引用編號時不附來源清單，結構化來源也必須清空。"""
    docs = [_doc("食藥署", "蜂蜜", "https://www.fda.gov.tw/b")]

    RagAnswerService._append_sources("這是一段沒有引用編號的答案。", docs)

    assert get_request_rag_sources() == ()


def test_structured_sources_keep_url_verbatim():
    """網址不得被改寫——line-reply-rules 明文要求。"""
    url = "https://www.fda.gov.tw/TC/siteContent.aspx?sid=1234&x=%E4%B8%AD"
    docs = [_doc("食藥署", "蜂蜜", url)]

    RagAnswerService._append_sources("放室溫即可 [1]。", docs)

    assert get_request_rag_sources()[0].url == url


def test_structured_sources_allow_missing_url():
    """缺 url 的來源仍須保留（rag-responses 明文要求不得靜默丟棄）。"""
    docs = [_doc("食藥署", "蜂蜜保存指引", "")]

    RagAnswerService._append_sources("放室溫即可 [1]。", docs)

    refs = get_request_rag_sources()
    assert len(refs) == 1
    assert refs[0].url == ""
```

- [ ] **Step 6: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_answer_service.py -k structured -v`
Expected: FAIL — `get_request_rag_sources()` 一律回傳 `()`，第一個測試在 `[r.index for r in refs] == [1, 2]` 掛掉

- [ ] **Step 7: 改 answer_service**

在 import 區加入：

```python
from app.core.rag_sources import SourceRef, set_request_rag_sources
```

在 `_source_key` 之後新增：

```python
    @staticmethod
    def _source_ref(doc: Document, index: int) -> SourceRef:
        """從 metadata 直接取值組成結構化來源。

        刻意不重用 `_source_label` 的輸出：那個字串是給純文字清單看的，
        用全形冒號把來源名與網址黏在一起，而來源名本身也可能含冒號，
        反解回來不可靠。
        """
        source = str(doc.metadata.get("source_name") or "").strip()
        title = str(doc.metadata.get("original_title") or "").strip()
        url = str(doc.metadata.get("url") or "").strip()
        label = source or title or f"來源 {index}"
        return SourceRef(index=index, label=label, url=url)
```

把 `_append_sources` 改為（僅列出變動處，其餘不動）：

```python
    @staticmethod
    def _append_sources(answer_text: str, docs: list[Document]) -> str:
        cited = cited_indices(answer_text)
        if not cited:
            logger.info("citation_missing docs=%d", len(docs))
            set_request_rag_sources(())
            return answer_text

        key_to_new: dict[str, int] = {}
        renumber: dict[int, int] = {}
        source_lines: list[str] = []
        source_refs: list[SourceRef] = []          # 新增

        for old_idx in cited:
            if old_idx < 1 or old_idx > len(docs):
                continue
            doc = docs[old_idx - 1]
            label = RagAnswerService._source_label(doc)
            if label is None:
                continue
            key = RagAnswerService._source_key(doc)
            existing = key_to_new.get(key)
            if existing is not None:
                renumber[old_idx] = existing
                continue
            if len(source_lines) >= CITE_TOP_K:
                continue
            new_idx = len(source_lines) + 1
            key_to_new[key] = new_idx
            renumber[old_idx] = new_idx
            source_lines.append(f"[{new_idx}] {label}")
            source_refs.append(                     # 新增：與文字清單同一個迴圈、
                RagAnswerService._source_ref(doc, new_idx)   # 同一個 new_idx，
            )                                       # 兩者編號因此不可能漂移

        def _replace(match: re.Match[str]) -> str:
            mapped = renumber.get(int(match.group(1)))
            return f"[{mapped}]" if mapped is not None else ""

        # 先改寫內文再決定要不要附清單：即使一筆來源都解析不出來，
        # 那些指向不存在來源的標記仍必須從答案中移除。
        body = _CITATION_RE.sub(_replace, answer_text)

        if not source_lines:
            logger.info("citation_unresolved cited=%s docs=%d", cited, len(docs))
            set_request_rag_sources(())
            return body

        set_request_rag_sources(source_refs)        # 新增
        heading = t("agent.sources_heading")
        return f"{body}\n\n{heading}\n" + "\n".join(source_lines)
```

**注意：** 三個 return 路徑都要設值。少設任何一個，上一輪的來源會殘留到這一輪，使用者會看到不屬於這個問題的來源按鈕。

- [ ] **Step 8: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_answer_service.py -v`
Expected: PASS（含既有測試全綠）

- [ ] **Step 9: Commit**

```bash
git add app/core/rag_sources.py app/services/rag/answer_service.py \
        tests/unit/core/test_rag_sources.py tests/unit/services/rag/test_answer_service.py
git commit -m "feat(rag): _append_sources 一併產出結構化參考來源

呈現層要把來源做成可點按鈕需要 (label, url)，而最終文字裡的來源是
「[1] 食藥署：https://...」這種字串，分隔符是全形冒號、來源名本身也可能
含冒號，反解不可靠。改為在攤平成文字的同一個迴圈裡收集 SourceRef，
兩者共用同一個 new_idx，編號不可能漂移。"
```

---

### Task 4: RAG 前綴剝除 helper

**Files:**
- Modify: `app/i18n/messages.py`（在 `all_sources_headings` 附近，約 1475 行）
- Test: `tests/unit/i18n/`（新增 `test_rag_prefix.py`）

**Interfaces:**
- Consumes: 無
- Produces: `all_rag_prefixes() -> frozenset[str]`、`strip_rag_prefix(text: str) -> str`

**Why:** 「以下為 RAG 回應：」是靠 `app/services/agent/prompt.py` 的 system prompt 約束的**軟規則**。卡片不放前綴，但不改 prompt——prompt 是軟約束，模型不保證照做；而純文字路徑（查不到、降級）仍然需要這個前綴。因此由呈現層剝除，剝除是確定性的。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/unit/i18n/test_rag_prefix.py
import pytest

from app.core.user_language import SUPPORTED_LANGUAGES
from app.i18n.messages import all_rag_prefixes, strip_rag_prefix, t


def test_all_rag_prefixes_covers_every_supported_language():
    prefixes = all_rag_prefixes()
    for lang in SUPPORTED_LANGUAGES:
        assert t("agent.rag_prefix", lang) in prefixes


@pytest.mark.parametrize("lang", SUPPORTED_LANGUAGES)
def test_strip_removes_prefix_for_every_language(lang):
    prefix = t("agent.rag_prefix", lang)

    assert strip_rag_prefix(f"{prefix}\n蜂蜜放室溫即可。") == "蜂蜜放室溫即可。"


def test_strip_is_noop_without_prefix():
    text = "蜂蜜放室溫即可。"
    assert strip_rag_prefix(text) == text


def test_strip_only_removes_leading_occurrence():
    """前綴出現在句中時不得刪除——那是答案內容的一部分。"""
    text = "使用者問：以下為 RAG 回應：是什麼意思？"
    assert strip_rag_prefix(text) == text


def test_strip_preserves_answer_body_whitespace_structure():
    prefix = t("agent.rag_prefix", "zh-TW")
    text = f"{prefix}\n\n第一段。\n\n第二段。"

    assert strip_rag_prefix(text) == "第一段。\n\n第二段。"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/i18n/test_rag_prefix.py -v`
Expected: FAIL — `ImportError: cannot import name 'all_rag_prefixes'`

- [ ] **Step 3: 寫實作**

在 `app/i18n/messages.py` 的 `all_sources_headings` 之後加入：

```python
def all_rag_prefixes() -> frozenset[str]:
    return frozenset(t("agent.rag_prefix", lang) for lang in SUPPORTED_LANGUAGES)


def strip_rag_prefix(text: str) -> str:
    """剝除回覆首行的 RAG 前綴。

    卡片路徑不放前綴：前綴的職責是告知「這段內容有外部來源」，卡片以 header
    與來源按鈕承擔同一職責，再放一行「以下為…」會與 header 重複。

    只剝除開頭：前綴字樣若出現在答案句中，那是內容的一部分，不能刪。
    """
    stripped = text.lstrip()
    for prefix in all_rag_prefixes():
        if stripped.startswith(prefix):
            return stripped[len(prefix) :].lstrip()
    return text
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/i18n/test_rag_prefix.py -v`
Expected: PASS（10 passed：4 個一般 + 6 個 parametrize）

- [ ] **Step 5: Commit**

```bash
git add app/i18n/messages.py tests/unit/i18n/test_rag_prefix.py
git commit -m "feat(i18n): 新增各語言 RAG 前綴的剝除 helper"
```

---

### Task 5: 卡片 builder

**Files:**
- Create: `app/services/line_messaging/flex/rag_answer_flex.py`
- Test: `tests/unit/services/line_messaging/flex/test_rag_answer_flex.py`

**Interfaces:**
- Consumes: Task 3 的 `SourceRef`；既有的 `resources.flex_messages.theme`
- Produces:
  - `build_rag_answer_flex(question: str, body: str, sources: Sequence[SourceRef], ft: FlexTheme) -> FlexMessage`
  - `build_document_answer_flex(question: str, body: str, ft: FlexTheme) -> FlexMessage`

**Design notes:**
- builder **只組裝**，不決定降級、不讀 ContextVar。`ft` 由呼叫端傳入，讓測試能直接指定字級而不必操作 ContextVar。
- 空字串會讓 LINE 以 400 拒收整則訊息（`verdict_flex.py` 已因此踩過，見該檔 `_BLANK_*_FALLBACK` 的註解），因此同樣要防禦。
- `url` 為空的來源不產生按鈕：URI action 沒有 uri 會被 LINE 拒收。但該筆仍存在於純文字清單中，符合 `rag-responses`「不得靜默丟棄」。

- [ ] **Step 1: 寫失敗測試**

```python
# tests/unit/services/line_messaging/flex/test_rag_answer_flex.py
import pytest

from app.core.rag_sources import SourceRef
from app.services.line_messaging.flex.rag_answer_flex import (
    build_document_answer_flex,
    build_rag_answer_flex,
)
from resources.flex_messages import theme
from resources.flex_messages.theme import _SIZE_SCALE


def _sources() -> list[SourceRef]:
    return [
        SourceRef(index=1, label="食藥署", url="https://www.fda.gov.tw/b"),
        SourceRef(index=2, label="台灣 e 院", url="https://sp1.hso.mohw.gov.tw/a"),
    ]


def _text_sizes(node: dict) -> list[str]:
    """遞迴收集 bubble 內所有 text 節點的 size。"""
    found: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "text" and "size" in node:
            found.append(node["size"])
        for value in node.values():
            found.extend(_text_sizes(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_text_sizes(item))
    return found


@pytest.mark.parametrize("font_size", ["normal", "large", "xlarge"])
def test_every_text_size_comes_from_the_scale(font_size):
    """本次功能的核心斷言：卡片文字大小必須跟著使用者字級設定走。

    卡片內每一個 text 節點的 size 都必須是 _SIZE_SCALE 中該字級那一欄的值，
    不得出現寫死的 keyword。
    """
    ft = theme.resolve_theme(font_size)
    allowed = {sizes[font_size] for sizes in _SIZE_SCALE.values()}

    msg = build_rag_answer_flex("蜂蜜怎麼保存？", "放室溫即可 [1]。", _sources(), ft)
    sizes = _text_sizes(msg.to_dict()["contents"])

    assert sizes, "卡片內應至少有一個帶 size 的 text 節點"
    assert set(sizes) <= allowed, f"出現不屬於 {font_size} 字級的 size：{set(sizes) - allowed}"


def test_larger_font_size_actually_produces_larger_keywords():
    """避免三種字級都「合法」卻其實一模一樣。"""
    normal = _text_sizes(
        build_rag_answer_flex("q", "a [1]。", _sources(), theme.resolve_theme("normal"))
        .to_dict()["contents"]
    )
    xlarge = _text_sizes(
        build_rag_answer_flex("q", "a [1]。", _sources(), theme.resolve_theme("xlarge"))
        .to_dict()["contents"]
    )

    assert normal != xlarge


def test_source_buttons_use_uri_action_with_verbatim_url():
    ft = theme.resolve_theme("large")
    sources = _sources()

    bubble = build_rag_answer_flex("q", "a [1][2]。", sources, ft).to_dict()["contents"]

    actions = [
        node["action"]
        for node in bubble["footer"]["contents"]
        if isinstance(node, dict) and "action" in node
    ]
    assert [a["type"] for a in actions] == ["uri", "uri"]
    assert [a["uri"] for a in actions] == [s.url for s in sources]
    assert "[1]" in actions[0]["label"] and "食藥署" in actions[0]["label"]


def test_source_without_url_produces_no_button():
    """URI action 缺 uri 會被 LINE 拒收；該筆仍留在純文字清單裡。"""
    ft = theme.resolve_theme("large")
    sources = [SourceRef(index=1, label="食藥署", url="")]

    bubble = build_rag_answer_flex("q", "a [1]。", sources, ft).to_dict()["contents"]

    assert "footer" not in bubble


def test_no_sources_means_no_footer():
    ft = theme.resolve_theme("large")

    bubble = build_rag_answer_flex("q", "沒有引用的答案。", [], ft).to_dict()["contents"]

    assert "footer" not in bubble


def test_document_card_has_no_source_section():
    """上傳文件問答不產生來源清單，卡片不得有來源區段。"""
    ft = theme.resolve_theme("large")

    bubble = build_document_answer_flex("這份報告說什麼？", "報告指出…", ft).to_dict()["contents"]

    assert "footer" not in bubble
    assert "參考資料來源" not in str(bubble)


def _all_text_values(node) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "text":
            found.append(node.get("text", ""))
        for value in node.values():
            found.extend(_all_text_values(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_all_text_values(item))
    return found


def test_blank_input_never_produces_empty_text_node():
    """空字串會讓 LINE 以 400 拒收整則訊息，每個 text 節點都必須有內容。"""
    ft = theme.resolve_theme("large")

    bubble = build_rag_answer_flex("", "", [], ft).to_dict()["contents"]

    values = _all_text_values(bubble)
    assert values, "卡片內應至少有一個 text 節點"
    assert all(v.strip() for v in values), f"出現空的 text 節點：{values}"


def test_alt_text_is_capped():
    """LINE altText 上限 400 字元，超過整則訊息會被拒收。"""
    ft = theme.resolve_theme("large")

    msg = build_rag_answer_flex("q", "衛" * 2000, [], ft)

    assert len(msg.alt_text) <= 400
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/services/line_messaging/flex/test_rag_answer_flex.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.line_messaging.flex.rag_answer_flex'`

- [ ] **Step 3: 寫實作**

```python
# app/services/line_messaging/flex/rag_answer_flex.py
"""RAG 回答卡：把已經產生好的答案文字組成 LINE Flex Message。

本模組只負責組裝。是否該走卡片、卡片太大要不要退回純文字，都由呼叫端
（reply.py）決定——把降級決策留在呈現層的單一出口，builder 才能保持
「輸入什麼就組出什麼」的單純性質，也才容易測。

字級不自己讀 ContextVar，改由呼叫端傳入 FlexTheme：測試要驗證三種字級的
輸出，傳參數比操作 request-scoped 狀態直接得多。

靜態文字寫死繁中，理由同 verdict_flex.py：卡片主體的答案本文由上游依
使用者語言生成，把「你問的」這幾個字 i18n 而主體是另一種語言，只會生出
半中半外的卡片。若日後要多語系，應與答案生成的語言一起處理。
"""

from __future__ import annotations

from typing import Any, Sequence

from linebot.v3.messaging import FlexContainer, FlexMessage

from app.core.rag_sources import SourceRef
from resources.flex_messages import theme

_HEADER_RAG = "衛教資訊"
_HEADER_DOCUMENT = "文件內容問答"
_QUESTION_LABEL = "你問的"
_SOURCES_LABEL = "參考資料來源"

# LINE altText 官方上限 400 字元，超過會讓整則訊息在送出時被拒絕。
_ALT_TEXT_MAX_LEN = 400

# LINE Flex 的 text 元件不接受空字串，空字串會讓整則訊息在 API 呼叫時直接被
# 拒收（400），使用者什麼都收不到——比顯示一句不完美的預設文字更糟。
# verdict_flex.py 已因同一個原因踩過這個坑。
_BLANK_QUESTION_FALLBACK = "（無法取得原始問句內容）"
_BLANK_BODY_FALLBACK = "（暫無內容，請換個方式再問一次）"


def _header(title: str, ft: theme.FlexTheme) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": theme.BRAND,
        "paddingAll": "lg",
        "contents": [
            {
                "type": "text",
                "text": title,
                "size": ft.heading,
                "color": theme.TEXT_ON_BRAND,
                "weight": "bold",
                "wrap": True,
            }
        ],
    }


def _question_block(question: str, ft: theme.FlexTheme) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "backgroundColor": theme.SURFACE_ALT,
        "cornerRadius": "md",
        "paddingAll": "lg",
        "spacing": "xs",
        "contents": [
            {
                "type": "text",
                "text": _QUESTION_LABEL,
                "size": ft.caption,
                "color": theme.TEXT_FAINT,
                "wrap": True,
            },
            {
                "type": "text",
                "text": question.strip() or _BLANK_QUESTION_FALLBACK,
                "size": ft.body,
                "color": theme.TEXT,
                "weight": "bold",
                "wrap": True,
            },
        ],
    }


def _body_text(body: str, ft: theme.FlexTheme) -> dict[str, Any]:
    return {
        "type": "text",
        "text": body.strip() or _BLANK_BODY_FALLBACK,
        "size": ft.body,
        "color": theme.TEXT_MUTED,
        "wrap": True,
    }


def _source_buttons(
    sources: Sequence[SourceRef], ft: theme.FlexTheme
) -> list[dict[str, Any]]:
    """把來源做成可點的 URI action 按鈕。

    url 為空的來源略過：URI action 沒有 uri 會被 LINE 拒收。該筆仍存在於
    純文字的來源清單中，符合 rag-responses「缺 url 不得靜默丟棄」的要求——
    這裡略過的是按鈕，不是來源本身。
    """
    return [
        ft.secondary_button(
            f"[{source.index}] {source.label}",
            {"type": "uri", "label": f"[{source.index}]", "uri": source.url},
        )
        for source in sources
        if source.url.strip()
    ]


def _alt_text(header: str, body: str) -> str:
    summary = " ".join((body or "").split())
    text = f"{header}｜{summary}" if summary else header
    return text[:_ALT_TEXT_MAX_LEN]


def _bubble(
    header_title: str,
    question: str,
    body: str,
    buttons: list[dict[str, Any]],
    ft: theme.FlexTheme,
) -> dict[str, Any]:
    contents: list[dict[str, Any]] = [
        _question_block(question, ft),
        _body_text(body, ft),
    ]
    if buttons:
        contents.append({"type": "separator", "margin": "lg", "color": theme.BORDER})
        section = ft.section_title(_SOURCES_LABEL)
        section["margin"] = "lg"
        contents.append(section)

    bubble: dict[str, Any] = {
        "type": "bubble",
        "header": _header(header_title, ft),
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "xl",
            "spacing": "md",
            "contents": contents,
        },
    }
    if buttons:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "lg",
            "contents": buttons,
        }
    return bubble


def build_rag_answer_flex(
    question: str,
    body: str,
    sources: Sequence[SourceRef],
    ft: theme.FlexTheme,
) -> FlexMessage:
    """衛教問答卡（get_rag_answer）。"""
    buttons = _source_buttons(sources, ft)
    bubble = _bubble(_HEADER_RAG, question, body, buttons, ft)
    return FlexMessage(
        altText=_alt_text(_HEADER_RAG, body),
        contents=FlexContainer.from_dict(bubble),
    )


def build_document_answer_flex(
    question: str,
    body: str,
    ft: theme.FlexTheme,
) -> FlexMessage:
    """上傳文件問答卡（answer_from_uploaded_document）。

    沒有來源區段：UserDocumentAnswerService.answer() 只回傳答案本文，不產生
    來源清單。header 文案與衛教卡區隔，避免使用者以為這是知識庫的內容。
    """
    bubble = _bubble(_HEADER_DOCUMENT, question, body, [], ft)
    return FlexMessage(
        altText=_alt_text(_HEADER_DOCUMENT, body),
        contents=FlexContainer.from_dict(bubble),
    )
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/services/line_messaging/flex/test_rag_answer_flex.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/line_messaging/flex/rag_answer_flex.py \
        tests/unit/services/line_messaging/flex/test_rag_answer_flex.py
git commit -m "feat(flex): 新增 RAG 回答卡與文件問答卡 builder

所有文字尺寸取自傳入的 FlexTheme，因此跟著使用者的 font_size 設定走。
builder 只負責組裝，降級決策留在呼叫端。"
```

---

### Task 6: agent 回報 answer_kind

**Files:**
- Modify: `app/tools/user_document_tools.py`（提升訊息為常數 + 新增判斷函式）
- Modify: `app/services/agent/agent.py`（`invoke()` 結尾，約 250-267 行）
- Test: `tests/unit/tools/test_user_document_tools.py`（新增）、`tests/unit/services/agent/test_agent.py`（追加）

**Interfaces:**
- Consumes: 既有的 `app.services.rag.fail_messages.is_rag_fail`
- Produces: `is_document_answer_unavailable(text: str) -> bool`；`Agent.invoke()` 回傳的 dict 新增鍵 `answer_kind: str | None`，值為 `"rag"` / `"document"` / `None`

**Why:** 呈現層要知道「這輪是不是有內容可以做成卡片」。判斷放在 agent 而非 reply，因為只有 agent 看得到 ToolMessage。

`answer_kind` 為 `None` 的三種情形，缺一不可：
1. 本輪沒跑 RAG 系工具
2. 工具輸出是失敗訊息（`is_rag_fail()` 或 `is_document_answer_unavailable()`）——沒有內容可呈現，包成卡片沒有意義
3. `used_tool_names` 非空——response 已被 `verify_claim` 之類的工具接管、內容是 Flex JSON，再組一次卡會壞掉

- [ ] **Step 1: 寫 user_document_tools 的失敗測試**

```python
# tests/unit/tools/test_user_document_tools.py
from app.services.rag.user_document_answer_service import NO_DOCS_MESSAGE
from app.tools.user_document_tools import (
    SERVICE_UNAVAILABLE_MESSAGE,
    UNKNOWN_USER_MESSAGE,
    is_document_answer_unavailable,
)


def test_no_docs_message_is_unavailable():
    assert is_document_answer_unavailable(NO_DOCS_MESSAGE) is True


def test_service_and_user_errors_are_unavailable():
    assert is_document_answer_unavailable(SERVICE_UNAVAILABLE_MESSAGE) is True
    assert is_document_answer_unavailable(UNKNOWN_USER_MESSAGE) is True


def test_real_answer_is_available():
    assert is_document_answer_unavailable("報告指出你的血壓偏高 [1]。") is False


def test_blank_is_unavailable():
    assert is_document_answer_unavailable("") is True
    assert is_document_answer_unavailable(None) is True
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/tools/test_user_document_tools.py -v`
Expected: FAIL — `ImportError: cannot import name 'SERVICE_UNAVAILABLE_MESSAGE'`

- [ ] **Step 3: 改 user_document_tools.py**

```python
from app.services.rag.user_document_answer_service import NO_DOCS_MESSAGE

SERVICE_UNAVAILABLE_MESSAGE = "上傳文件問答服務未初始化，請稍後再試。"
UNKNOWN_USER_MESSAGE = "無法取得使用者身分，請稍後再試。"


def is_document_answer_unavailable(text: str | None) -> bool:
    """這段工具輸出是不是「沒有內容可呈現」。

    上傳文件問答沒有走 fail_messages 的 [RAG_ERR:] 前綴機制（那是知識庫
    RAG 專用的），因此改以列舉三個固定訊息判斷。列舉而非模糊比對：這三個
    字串是本模組與 UserDocumentAnswerService 自己產生的，不是外部輸入。
    """
    stripped = (text or "").strip()
    if not stripped:
        return True
    return stripped in {
        NO_DOCS_MESSAGE,
        SERVICE_UNAVAILABLE_MESSAGE,
        UNKNOWN_USER_MESSAGE,
    }
```

並把 `answer_from_uploaded_document` 內兩處寫死的字串改成引用上述常數。

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/tools/test_user_document_tools.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 寫 agent 的失敗測試**

```python
# 追加到 tests/unit/services/agent/test_agent.py
@pytest.mark.asyncio
async def test_invoke_reports_rag_answer_kind(mock_llm, mock_guardrail_service):
    from langchain_core.messages import ToolMessage

    agent = Agent(llm=mock_llm, guardrail_service=mock_guardrail_service)
    agent._graph = MagicMock()
    agent._graph.ainvoke = AsyncMock(
        return_value={
            "messages": [
                HumanMessage(content="蜂蜜怎麼保存？"),
                ToolMessage(content="放室溫即可 [1]。", tool_call_id="1", name="get_rag_answer"),
                AIMessage(content="放室溫即可。"),
            ]
        }
    )

    result = await agent.invoke(user_input="蜂蜜怎麼保存？", messages=None)

    assert result["answer_kind"] == "rag"


@pytest.mark.asyncio
async def test_invoke_reports_none_for_rag_failure(mock_llm, mock_guardrail_service):
    """查不到時沒有內容可呈現，不該做成卡片。"""
    from app.services.rag.fail_messages import RagFailCode, rag_fail
    from langchain_core.messages import ToolMessage

    agent = Agent(llm=mock_llm, guardrail_service=mock_guardrail_service)
    agent._graph = MagicMock()
    agent._graph.ainvoke = AsyncMock(
        return_value={
            "messages": [
                HumanMessage(content="蜂蜜可以治癌症嗎？"),
                ToolMessage(
                    content=rag_fail(RagFailCode.KB_EMPTY),
                    tool_call_id="1",
                    name="get_rag_answer",
                ),
                AIMessage(content="請換個方式描述。"),
            ]
        }
    )

    result = await agent.invoke(user_input="蜂蜜可以治癌症嗎？", messages=None)

    assert result["answer_kind"] is None


@pytest.mark.asyncio
async def test_invoke_reports_none_when_flex_tool_took_over(
    mock_llm, mock_guardrail_service
):
    """verify_claim 已接管 response（內容是 Flex JSON），不得再組一次卡。"""
    from langchain_core.messages import ToolMessage

    agent = Agent(llm=mock_llm, guardrail_service=mock_guardrail_service)
    agent._graph = MagicMock()
    agent._graph.ainvoke = AsyncMock(
        return_value={
            "messages": [
                HumanMessage(content="網傳蜂蜜可以抗癌"),
                ToolMessage(content="衛教內容 [1]。", tool_call_id="1", name="get_rag_answer"),
                ToolMessage(
                    content='{"type": "flex", "altText": "判定", "contents": {}}',
                    tool_call_id="2",
                    name="verify_claim",
                ),
                AIMessage(content="這則說法尚未查證。"),
            ]
        }
    )

    result = await agent.invoke(user_input="網傳蜂蜜可以抗癌", messages=None)

    assert result["answer_kind"] is None


@pytest.mark.asyncio
async def test_invoke_reports_document_answer_kind(mock_llm, mock_guardrail_service):
    from langchain_core.messages import ToolMessage

    agent = Agent(llm=mock_llm, guardrail_service=mock_guardrail_service)
    agent._graph = MagicMock()
    agent._graph.ainvoke = AsyncMock(
        return_value={
            "messages": [
                HumanMessage(content="這份報告說什麼？"),
                ToolMessage(
                    content="報告指出血壓偏高 [1]。",
                    tool_call_id="1",
                    name="answer_from_uploaded_document",
                ),
                AIMessage(content="報告指出血壓偏高。"),
            ]
        }
    )

    result = await agent.invoke(user_input="這份報告說什麼？", messages=None)

    assert result["answer_kind"] == "document"
```

- [ ] **Step 6: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/services/agent/test_agent.py -k answer_kind -v`
Expected: FAIL — `KeyError: 'answer_kind'`

- [ ] **Step 7: 改 agent.py**

在 import 區加入：

```python
from app.services.rag.fail_messages import is_rag_fail
from app.tools.user_document_tools import is_document_answer_unavailable
```

在 `invoke()` 的 `return` 之前加入：

```python
        # 呈現層要知道「這輪是不是有內容可以做成卡片」。判斷放在這裡而非
        # reply.py，因為只有這裡看得到 ToolMessage。
        answer_kind: str | None = None
        if not used_tool_names:
            # used_tool_names 非空代表 response 已被醫療工具接管、內容是要原封
            # 不動送出的 Flex JSON，再組一次卡只會壞掉（同「後補來源」那段的
            # 理由）。
            for msg in reversed(result.get("messages", [])):
                name = getattr(msg, "name", None)
                if name not in ("get_rag_answer", "answer_from_uploaded_document"):
                    continue
                content = msg.content if isinstance(msg.content, str) else str(msg.content)
                if name == "get_rag_answer":
                    answer_kind = None if is_rag_fail(content) else "rag"
                else:
                    answer_kind = (
                        None if is_document_answer_unavailable(content) else "document"
                    )
                break

        logger.info(
            "[Agent] 執行完成，response_type=%s, call_request_location=%s, answer_kind=%s",
            type(response).__name__,
            call_request_location,
            answer_kind,
        )

        return {
            "response": response,
            "call_request_location": call_request_location,
            "answer_kind": answer_kind,
        }
```

（原本那段 `logger.info("[Agent] 執行完成", ...)` 與 `return` 一併被上面取代。）

- [ ] **Step 8: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/services/agent/test_agent.py -v`
Expected: PASS（含既有測試全綠）

- [ ] **Step 9: Commit**

```bash
git add app/tools/user_document_tools.py app/services/agent/agent.py \
        tests/unit/tools/test_user_document_tools.py tests/unit/services/agent/test_agent.py
git commit -m "feat(agent): invoke 回報 answer_kind 供呈現層決定是否組卡

失敗訊息與已被 Flex 工具接管的回覆一律為 None：前者沒有內容可呈現，
後者的 response 已經是 Flex JSON，再組一次卡會壞掉。"
```

---

### Task 7: reply 組卡、降級與語音

**Files:**
- Modify: `app/services/line_messaging/reply/reply.py`（`reply()` 約 47-118 行，新增 `_build_answer_card`）
- Test: `tests/unit/services/line_messaging/test_reply.py`（既有檔案，追加）

**Interfaces:**
- Consumes: Task 1 `fits`、Task 3 `get_request_rag_sources`、Task 4 `strip_rag_prefix`、Task 5 兩個 builder、既有的 `strip_sources_section`、`resolve_theme`
- Produces: `LineReplier.reply()` 新增兩個 keyword 參數 `answer_kind: str | None = None`、`user_question: str = ""`

**關鍵設計：**
- **工具自產的 Flex（`verify_claim`／`open_official_site`）行為完全不變**，尤其**不新增語音**。本次只為自組的 RAG 卡加語音；改動判定卡的語音行為是範圍外的行為變更。
- 組卡前要**移除文字版的來源區段**（`strip_sources_section`），否則來源會同時出現在卡片內文與按鈕上。
- TTS 合成用的是**組卡前的純文字**（已剝前綴、已移除來源清單），不是卡片 JSON。

- [ ] **Step 1: 寫失敗測試**

```python
# 追加到 tests/unit/services/line_messaging/test_reply.py
from linebot.v3.messaging import FlexMessage, TextMessage

from app.core.rag_sources import SourceRef, reset_request_rag_sources, set_request_rag_sources
from app.i18n.messages import t


@pytest.mark.asyncio
async def test_rag_answer_kind_sends_flex(replier):
    token = set_request_rag_sources(
        [SourceRef(index=1, label="食藥署", url="https://www.fda.gov.tw/b")]
    )
    try:
        ok, messaging_api = await _send_reply(
            replier,
            reply_token="rt",
            message_text=f"{t('agent.rag_prefix', 'zh-TW')}\n蜂蜜放室溫即可 [1]。\n\n"
            f"{t('agent.sources_heading', 'zh-TW')}\n[1] 食藥署：https://www.fda.gov.tw/b",
            user_id="U1",
            voice_reply_enabled=False,
            answer_kind="rag",
            user_question="蜂蜜怎麼保存？",
        )
    finally:
        reset_request_rag_sources(token)

    assert ok is True
    sent = messaging_api.reply_message.call_args[0][0].messages
    assert isinstance(sent[0], FlexMessage)

    bubble = sent[0].contents.to_dict() if hasattr(sent[0].contents, "to_dict") else sent[0].contents
    rendered = str(bubble)
    assert "蜂蜜怎麼保存？" in rendered
    assert t("agent.rag_prefix", "zh-TW") not in rendered, "卡片不得含 RAG 前綴"
    assert "https://www.fda.gov.tw/b" in rendered, "來源網址應出現在按鈕的 uri"
    assert rendered.count("https://www.fda.gov.tw/b") == 1, "來源不得同時出現在內文與按鈕"


@pytest.mark.asyncio
async def test_no_answer_kind_still_sends_text(replier):
    ok, messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text="一般閒聊回覆。",
        user_id="U1",
        voice_reply_enabled=False,
    )

    assert ok is True
    assert isinstance(messaging_api.reply_message.call_args[0][0].messages[0], TextMessage)


@pytest.mark.asyncio
async def test_oversized_rag_card_falls_back_to_text(replier):
    """卡片太大時退回純文字，使用者仍拿得到內容。"""
    long_answer = "衛" * 4000

    ok, messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text=long_answer,
        user_id="U1",
        voice_reply_enabled=False,
        answer_kind="rag",
        user_question="蜂蜜怎麼保存？",
    )

    assert ok is True
    sent = messaging_api.reply_message.call_args[0][0].messages[0]
    assert isinstance(sent, TextMessage)
    assert sent.text == long_answer


@pytest.mark.asyncio
async def test_builder_failure_falls_back_to_text(replier, monkeypatch):
    """builder 拋例外時退回純文字，不得讓使用者拿到空白回覆。

    這裡 patch 的是本模組自己 import 的 builder（呈現層內部細節），
    不是應用層依賴的注入點，因此不違反「以 DI 傳入 mock」的規則。
    """
    def _boom(*args, **kwargs):
        raise RuntimeError("builder 壞了")

    monkeypatch.setattr(
        "app.services.line_messaging.reply.reply.build_rag_answer_flex", _boom
    )

    ok, messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text="蜂蜜放室溫即可。",
        user_id="U1",
        voice_reply_enabled=False,
        answer_kind="rag",
        user_question="蜂蜜怎麼保存？",
    )

    assert ok is True
    assert isinstance(messaging_api.reply_message.call_args[0][0].messages[0], TextMessage)


@pytest.mark.asyncio
async def test_flex_branch_appends_audio_when_voice_enabled():
    """開了語音回覆的使用者不該在 RAG 回覆上靜默失去語音。"""
    fake_tts = FakeTTSService()
    replier = LineReplier(token_manager=fake_line_token_manager("token"), tts_service=fake_tts)

    ok, messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text=f"{t('agent.rag_prefix', 'zh-TW')}\n蜂蜜放室溫即可。",
        user_id="U1",
        voice_reply_enabled=True,
        answer_kind="rag",
        user_question="蜂蜜怎麼保存？",
    )

    assert ok is True
    sent = messaging_api.reply_message.call_args[0][0].messages
    assert len(sent) == 2
    assert isinstance(sent[0], FlexMessage)
    assert sent[1].original_content_url == "https://example.com/audio.mp3"
    assert fake_tts.calls[0]["text"] == "蜂蜜放室溫即可。", "朗讀的是組卡前的純文字，且不含前綴"


@pytest.mark.asyncio
async def test_flex_branch_skips_audio_when_voice_disabled():
    fake_tts = FakeTTSService()
    replier = LineReplier(token_manager=fake_line_token_manager("token"), tts_service=fake_tts)

    ok, messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text="蜂蜜放室溫即可。",
        user_id="U1",
        voice_reply_enabled=False,
        answer_kind="rag",
        user_question="蜂蜜怎麼保存？",
    )

    assert ok is True
    assert len(messaging_api.reply_message.call_args[0][0].messages) == 1
    assert fake_tts.calls == []


@pytest.mark.asyncio
async def test_tool_flex_still_has_no_audio():
    """工具自產的 Flex（判定卡、官網卡）行為不變：本次不為它們新增語音。"""
    fake_tts = FakeTTSService()
    replier = LineReplier(token_manager=fake_line_token_manager("token"), tts_service=fake_tts)

    ok, messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text='{"type": "flex", "altText": "判定", "contents": {"type": "bubble"}}',
        user_id="U1",
        voice_reply_enabled=True,
    )

    assert ok is True
    assert len(messaging_api.reply_message.call_args[0][0].messages) == 1
    assert fake_tts.calls == []


@pytest.mark.asyncio
async def test_quick_reply_still_on_last_message(replier):
    """位置 Quick Reply 掛在最後一則的既有行為不得改變。"""
    ok, messaging_api = await _send_reply(
        replier,
        reply_token="rt",
        message_text="請分享你的位置。",
        user_id="U1",
        request_location=True,
        voice_reply_enabled=False,
    )

    assert ok is True
    sent = messaging_api.reply_message.call_args[0][0].messages
    assert sent[-1].quick_reply is not None
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/services/line_messaging/test_reply.py -k "rag or flex_branch or tool_flex or answer_kind" -v`
Expected: FAIL — `TypeError: reply() got an unexpected keyword argument 'answer_kind'`

- [ ] **Step 3: 改 reply.py**

import 區加入：

```python
from app.core.rag_sources import get_request_rag_sources
from app.i18n.messages import strip_rag_prefix, strip_sources_section
from app.services.line_messaging.flex.rag_answer_flex import (
    build_document_answer_flex,
    build_rag_answer_flex,
)
from resources.flex_messages.size_guard import fits
from resources.flex_messages.theme import resolve_theme
```

`reply()` 簽名新增兩個參數：

```python
        voice_gender: str = "female",
        answer_kind: str | None = None,
        user_question: str = "",
    ) -> bool:
```

把原本 `flex_message = self._try_parse_flex_message(...)` 到 `messages = [text_message]` 那一段換成：

```python
            # 工具自產的 Flex（verify_claim、open_official_site）原樣送出，
            # 行為完全不變——包括不附加語音。
            tool_flex = self._try_parse_flex_message(message_text)
            if tool_flex is not None:
                logger.info(
                    f"{LOGGER_HEADER_TEXT} 解析為工具 Flex Message，將以 Flex 形式回覆"
                )
                messages = [tool_flex]
            else:
                answer_card, card_text = self._build_answer_card(
                    message_text, answer_kind, user_question
                )
                if answer_card is not None:
                    logger.info(
                        f"{LOGGER_HEADER_TEXT} 已組成 %s 回答卡，將以 Flex 形式回覆",
                        answer_kind,
                    )
                    messages = [answer_card]
                    # 卡片路徑同樣附加語音：只有純文字分支有語音的話，開了語音
                    # 回覆的使用者會在 RAG 回覆上靜默失去這個功能。合成用的是
                    # 組卡前的純文字，不是卡片 JSON。
                    await self._append_tts_audio_message(
                        messages,
                        card_text,
                        voice_reply_enabled=voice_reply_enabled,
                        language=language,
                        voice_rate=voice_rate,
                        voice_gender=voice_gender,
                    )
                else:
                    logger.info(
                        f"{LOGGER_HEADER_TEXT} 未組成卡片，將以純文字回覆"
                    )
                    messages = [TextMessage(text=message_text)]
                    await self._append_tts_audio_message(
                        messages,
                        message_text,
                        voice_reply_enabled=voice_reply_enabled,
                        language=language,
                        voice_rate=voice_rate,
                        voice_gender=voice_gender,
                    )
```

新增方法：

```python
    def _build_answer_card(
        self, message_text: str, answer_kind: Optional[str], user_question: str
    ) -> tuple[Optional[FlexMessage], str]:
        """把 RAG 回覆組成卡片。

        回傳 `(卡片, 卡片內用的純文字)`；組不出來、太大或 answer_kind 為 None
        時卡片為 None。純文字一併回傳是給 TTS 用的——朗讀的內容應與卡片一致。

        任何失敗都退回純文字而非拋出：呈現層是最後一步，使用者寧可拿到樸素
        的文字，也不能拿到空白回覆。
        """
        if answer_kind not in ("rag", "document"):
            return None, message_text

        # 前綴由卡片 header 取代；來源清單移到按鈕，留在內文會重複一次。
        card_text = strip_sources_section(strip_rag_prefix(message_text)).strip()

        try:
            ft = resolve_theme()
            if answer_kind == "rag":
                card = build_rag_answer_flex(
                    user_question, card_text, get_request_rag_sources(), ft
                )
            else:
                card = build_document_answer_flex(user_question, card_text, ft)

            if not fits(card.to_dict()["contents"]):
                logger.warning(
                    f"{LOGGER_HEADER_TEXT} %s 回答卡超過大小上限，改以純文字回覆",
                    answer_kind,
                )
                return None, message_text

            return card, card_text
        except Exception:
            logger.warning(
                f"{LOGGER_HEADER_TEXT} %s 回答卡組裝失敗，改以純文字回覆",
                answer_kind,
                exc_info=True,
            )
            return None, message_text
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/services/line_messaging/test_reply.py -v`
Expected: PASS（含既有測試全綠）

- [ ] **Step 5: Commit**

```bash
git add app/services/line_messaging/reply/reply.py tests/unit/services/line_messaging/test_reply.py
git commit -m "feat(line): RAG 回覆以 Flex 卡片送出，組不出來或太大即退回純文字

卡片在呈現層組裝，因此對話歷史存的仍是純文字。卡片路徑一併附加語音，
避免開了語音回覆的使用者在 RAG 回覆上靜默失去該功能；工具自產的 Flex
行為不變，仍不附語音。"
```

---

### Task 8: message_handler 接線

**Files:**
- Modify: `app/services/line_messaging/handler/message_handler.py`（約 68-69、105-110、148-165、191-195 行）
- Test: `tests/unit/services/line_messaging/test_message_handler.py`（既有檔案，追加）

**Interfaces:**
- Consumes: Task 3 `set_request_rag_sources`／`reset_request_rag_sources`、Task 6 的 `answer_kind`、Task 7 的新參數
- Produces: 無新介面

**兩件事：**
1. 把 `answer_kind` 與使用者原問句傳給 `replier.reply()`
2. 在每輪開頭把來源 ContextVar 重設為空，`finally` 還原——否則上一輪的來源會殘留成這一輪的按鈕

- [ ] **Step 1: 寫失敗測試**

```python
# 追加到 tests/unit/services/line_messaging/test_message_handler.py
#
# 既有的 `_handler()` 把 agent 與 history 寫死在函式內，這裡先把它們變成可
# 注入的參數（預設值維持原本的行為，既有測試不受影響），再加新測試。
#
# 把 _handler 的簽名改為：
#     def _handler(safety_alert_service=None, replier=None, agent=None, history_service=None):
# 並在 LineMessageHandler(...) 的呼叫中改用：
#     agent=agent or _Agent(),
#     history_service=history_service or _History(),


class RecordingAgent:
    """可指定回傳值的 agent；同時記下呼叫當下看到的 ContextVar 狀態。"""

    def __init__(self, response="蜂蜜放室溫即可 [1]。", answer_kind="rag"):
        self._response = response
        self._answer_kind = answer_kind
        self.seen_sources = None

    async def invoke(self, **kwargs):
        from app.core.rag_sources import get_request_rag_sources

        self.seen_sources = get_request_rag_sources()
        return {
            "response": self._response,
            "call_request_location": False,
            "answer_kind": self._answer_kind,
        }


class RecordingHistory:
    def __init__(self):
        self.saved = []

    async def load_history(self, **kwargs):
        return []

    async def save_turn(self, **kwargs):
        self.saved.append(kwargs)


@pytest.mark.asyncio
async def test_handler_passes_answer_kind_and_question_to_replier():
    """呈現層要靠這兩個值才組得出卡片。"""
    replier = FakeReplier()
    handler = _handler(replier=replier, agent=RecordingAgent())

    await handler.handle(_text_event())
    await _drain(handler)

    assert replier.replies[0]["answer_kind"] == "rag"
    assert replier.replies[0]["user_question"] == USER_TEXT


@pytest.mark.asyncio
async def test_handler_saves_plain_text_to_history_not_flex_json():
    """卡片在呈現層才組，因此存進歷史的必須仍是純文字。

    這正是不走 medical_tool_names 白名單的理由之一：那條路徑會把整包
    Flex JSON 存成 ai_reply，下一輪 agent 讀到自己上一則回覆是一大坨 JSON。
    """
    history = RecordingHistory()
    handler = _handler(agent=RecordingAgent(), history_service=history)

    await handler.handle(_text_event())
    await _drain(handler)

    saved = history.saved[0]["ai_reply"]
    assert saved == "蜂蜜放室溫即可 [1]。"
    assert not saved.strip().startswith("{")


@pytest.mark.asyncio
async def test_handler_clears_rag_sources_between_turns():
    """上一輪的來源不得殘留成這一輪的按鈕。"""
    from app.core.rag_sources import (
        SourceRef,
        get_request_rag_sources,
        reset_request_rag_sources,
        set_request_rag_sources,
    )

    leaked = set_request_rag_sources(
        [SourceRef(index=1, label="上一輪的來源", url="https://example.com/stale")]
    )
    try:
        agent = RecordingAgent(answer_kind=None)
        handler = _handler(agent=agent)

        await handler.handle(_text_event())
        await _drain(handler)

        assert agent.seen_sources == (), "進入 agent 前來源必須已清空"
    finally:
        reset_request_rag_sources(leaked)

    assert get_request_rag_sources()[0].label == "上一輪的來源", "handler 必須還原 ContextVar"
```

> **實作者注意：** `_handler`、`_text_event`、`FakeReplier`、`_drain`、`USER_TEXT` 都是這個測試檔既有的東西，沿用它們、不要另造一套。唯一要改的是把 `_handler` 的 agent 與 history 變成可注入參數（預設值保持原行為）。進入點是 `handler.handle(event)`（`message_handler.py:259`）。

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/services/line_messaging/test_message_handler.py -k "answer_kind or clears_rag_sources" -v`
Expected: FAIL — `KeyError: 'answer_kind'`（replier 收不到該參數）

- [ ] **Step 3: 改 message_handler.py**

import 區加入：

```python
from app.core.rag_sources import reset_request_rag_sources, set_request_rag_sources
```

在 `font_token = None` 旁加：

```python
        rag_sources_token = None
```

在 `font_token = set_request_font_size(...)` 之後加：

```python
            # 每輪開頭清空：上一輪的來源殘留下來，會變成這一輪卡片上不屬於
            # 這個問題的來源按鈕。
            rag_sources_token = set_request_rag_sources(())
```

`replier.reply(...)` 的呼叫加兩個參數：

```python
                voice_gender=voice_gender,
                answer_kind=agent_response.get("answer_kind"),
                user_question=user_text,
```

外層 `finally` 加：

```python
            if rag_sources_token is not None:
                reset_request_rag_sources(rag_sources_token)
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/services/line_messaging/ -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/line_messaging/handler/message_handler.py \
        tests/unit/services/line_messaging/test_message_handler.py
git commit -m "feat(line): handler 傳遞 answer_kind 與原問句，並管理來源 ContextVar 生命週期"
```

---

### Task 9: RAG 答案長度上限

**Files:**
- Modify: `app/services/rag/answer_prompts.py`（`build_rag_prompt`、`build_user_document_prompt`、`build_web_prompt`）
- Test: `tests/unit/services/rag/test_answer_prompts.py`（既有檔案，追加）

**Interfaces:**
- Consumes: 無
- Produces: 模組常數 `ANSWER_MAX_CHARS = 450`

**Why:** 讓超限在建構上不可能發生，而不是靠降級路徑事後補救。實測本次卡片版型的答案本文上限約 1,400 字，450 字留了三倍餘裕。這也不只是為了繞開技術限制——對高齡使用者而言，LINE 卡片裡塞上千字本來就是壞設計。

**三個 prompt 都要改**（規格 proposal 只寫了兩條生成路徑，實際上 `build_user_document_prompt` 也會產出卡片內容，同樣需要約束）。

- [ ] **Step 1: 寫失敗測試**

```python
# 追加到 tests/unit/services/rag/test_answer_prompts.py
import pytest

from app.services.rag.answer_prompts import (
    ANSWER_MAX_CHARS,
    build_rag_prompt,
    build_user_document_prompt,
    build_web_prompt,
)


@pytest.mark.parametrize(
    "builder", [build_rag_prompt, build_user_document_prompt, build_web_prompt]
)
def test_every_answer_prompt_carries_length_limit(builder):
    """三個生成路徑的輸出都會進卡片，都要受長度約束。"""
    rendered = builder("zh-TW").format(question="q", context="c")

    assert str(ANSWER_MAX_CHARS) in rendered


def test_length_limit_leaves_headroom_below_card_capacity():
    """卡片版型可容納約 1,400 字；上限須留足餘裕，讓降級不成為常態。"""
    assert 400 <= ANSWER_MAX_CHARS <= 500
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_answer_prompts.py -k length -v`
Expected: FAIL — `ImportError: cannot import name 'ANSWER_MAX_CHARS'`

- [ ] **Step 3: 改 answer_prompts.py**

在模組常數區加入：

```python
# 答案字數上限。實測本專案的衛教卡版型（large 字級、三個來源按鈕）骨架
# 1,839 bytes，答案本文可用 8,401 bytes，換算約 1,400 個中文字；450 字留了
# 三倍餘裕，讓「超過 LINE 上限就退回純文字」保持在防線的位置，而不是變成
# 經常走的路。
#
# 這也不只是技術限制的結果：本專案的使用者以長輩為主，LINE 卡片裡塞上千字
# 本來就不會有人讀完。約束寫在 prompt 而非事後截斷——截斷會在句子中間切斷，
# 且衛教內容的警示語常在最後一段，截掉的正好是最不該掉的部分。
ANSWER_MAX_CHARS = 450
```

三個 builder 各加一條規則（接在既有的最後一條規則之後，編號順延）：

`build_rag_prompt`（原第 6 條之後）：

```python
                f"6. {_BOUNDARY_RULE}\n"
                f"7. 整段回答請控制在 {ANSWER_MAX_CHARS} 字以內，"
                "只寫最重要的重點；寧可少寫也不要寫得又長又雜。\n\n"
```

`build_user_document_prompt`（原第 4 條之後）：

```python
                f"4. {_BOUNDARY_RULE}\n"
                f"5. 整段回答請控制在 {ANSWER_MAX_CHARS} 字以內，"
                "只寫最重要的重點；寧可少寫也不要寫得又長又雜。\n\n"
```

`build_web_prompt`（原第 5 條之後）：

```python
                f"5. {_BOUNDARY_RULE}\n"
                f"6. 整段回答請控制在 {ANSWER_MAX_CHARS} 字以內，"
                "只寫最重要的重點；寧可少寫也不要寫得又長又雜。\n\n"
```

- [ ] **Step 4: 跑測試確認通過**

Run: `.venv/bin/python -m pytest tests/unit/services/rag/test_answer_prompts.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/rag/answer_prompts.py tests/unit/services/rag/test_answer_prompts.py
git commit -m "feat(rag): 生成 prompt 加入答案字數上限

讓超出卡片容量在建構上不可能發生，而不是靠降級路徑事後補救。450 字對
卡片的 1,400 字容量留了三倍餘裕，對長輩讀者也更合適。"
```

---

## 驗收

- [ ] **A1:** `./init.sh` 全綠（所有 pytest 通過）
- [ ] **A2:** 跑 `evals/rag` 的 golden set，觀察答案長度分布與引用正確率是否因 `ANSWER_MAX_CHARS` 退步。若 recall 或引用正確率下降，回頭調整上限值，並把實測到的長度分布補進 `openspec/changes/rag-answer-card/design.md` 的「已知的證據缺口」一節——那份 spec 明白記載了這項資料目前缺席。
- [ ] **A3:** 真機確認三種字級（normal／large／xlarge）的卡片外觀，以及來源按鈕可正常開啟瀏覽器
- [ ] **A4:** 真機確認查不到資料時仍是純文字、且首行有 RAG 前綴
- [ ] **A5:** 真機確認開啟語音回覆時，RAG 卡片之後仍有語音訊息
- [ ] **A6:** `openspec archive rag-answer-card`
