# RAG Refuse Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** MODEL_REFUSE 時 log `matched_marker` + `answer_preview`，可應證誤殺 vs 真拒答。

**Architecture:** 共用 helper 找第一個 marker；KB／Web refuse 分支寫同一格式 info log；行為不變。

**Tech Stack:** Python 3.12, pytest, logging

## Global Constraints

- 不改 CANNOT_ANSWER_MARKERS 清單、不改對外 fail 文案、不加 web fallback
- preview 上限 200 字元
- empty 內容 `matched_marker=<empty>`
- Workdir：`/Users/jamessu/Desktop/computersciencehomework/CARE`，可直接在 `main` 或短分支實作後 commit
- OpenSpec：`openspec/changes/rag-refuse-diagnostics/`

---

### Task 1: Helper + KB/Web refuse diagnostics

**Files:**
- Create or modify: `app/services/rag/cannot_answer.py`（或放 `answer_service` 並讓 web import）— prefer small shared module to avoid circular imports
- Modify: `answer_service.py`, `web_search_service.py`
- Tests: `tests/unit/services/rag/test_cannot_answer.py`, update `test_answer_service.py` / `test_web_search_service.py`
- Check openspec tasks.md

- [ ] **Step 1: RED** — tests for `matched_cannot_answer_marker` and log kwargs on MODEL_REFUSE
- [ ] **Step 2: GREEN** — implement helper + logging both paths
- [ ] **Step 3: pytest** focused suite green
- [ ] **Step 4: Commit** `fix(rag): log matched_marker and preview on MODEL_REFUSE`

Suggested helper API:

```python
def matched_cannot_answer_marker(text: str, markers: tuple[str, ...]) -> str:
    normalized = (text or "").strip()
    if not normalized:
        return "<empty>"
    for m in markers:
        if m in normalized:
            return m
    return "<empty>"  # or "<none>" — for refuse path should always hit; use "<empty>" only for blank

def answer_preview(text: str, limit: int = 200) -> str:
    ...
```

Log example:
`logger.info("rag_fail code=%s matched_marker=%s answer_preview=%s", code, marker, preview)`
