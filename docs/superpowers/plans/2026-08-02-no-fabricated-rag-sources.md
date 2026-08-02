# No Fabricated RAG Sources Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 當 `get_rag_answer` 未附真實來源時，最終回覆不得出現亂編的參考資料清單。

**Architecture:** prompt 規則收緊 + agent 後置硬剝離（不依賴模型）。

**Tech Stack:** Python 3.12, pytest

## Global Constraints

- Evidence: prod `has_sources=False` but user saw fake source URLs
- Do not invent URLs; strip sources section if tool lacked heading
- Keep existing append-when-tool-has-sources behavior
- Workdir: `/Users/jamessu/Desktop/computersciencehomework/CARE`
- OpenSpec: `openspec/changes/no-fabricated-rag-sources/`

---

### Task 1: Prompt + strip post-process

**Files:**
- Modify: `app/i18n/messages.py` (add `strip_sources_section`) OR small helper next to existing heading helpers
- Modify: `app/services/agent/prompt.py`, `app/services/agent/agent.py`
- Tests: `tests/unit/i18n/` or agent tests; `test_prompt.py` for rule text
- Check openspec tasks.md

- [ ] RED/GREEN: strip helper; agent path: tool no sources + response with heading → stripped
- [ ] Prompt rule 8 updated for both cases
- [ ] tool has sources → still backfill if missing
- [ ] Commit `fix(agent): strip fabricated RAG sources when tool has none`
