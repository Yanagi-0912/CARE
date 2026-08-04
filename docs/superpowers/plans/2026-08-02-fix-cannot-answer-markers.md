# Fix Cannot-Answer Markers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 移除裸 `無法` 誤殺；共用 marker 清單；回歸測試護住河魨案例。

**Architecture:** `CANNOT_ANSWER_MARKERS` 單一定義於 `cannot_answer.py`；answer/web 匯入。

**Tech Stack:** Python 3.12, pytest

## Global Constraints

- Evidence from prod: `matched_marker=無法` on「無法透過加熱破壞」
- MUST NOT keep bare `"無法"` in markers
- MUST keep refuse for `無法提供` / `不知道` style phrases
- Workdir: `/Users/jamessu/Desktop/computersciencehomework/CARE`
- OpenSpec: `openspec/changes/fix-cannot-answer-markers/`

---

### Task 1: Shared precise markers + regression tests

**Files:**
- Modify: `app/services/rag/cannot_answer.py`, `answer_service.py`, `web_search_service.py`
- Modify: `tests/unit/services/rag/test_cannot_answer.py`, `test_answer_service.py`, `test_web_search_service.py` as needed
- Check: openspec tasks.md

- [ ] RED: test「無法透過加熱破壞」→ not refuse；「無法提供」→ refuse
- [ ] GREEN: move markers, remove bare 無法, wire imports
- [ ] pytest green; commit `fix(rag): tighten cannot-answer markers to avoid 無法 false positive`
