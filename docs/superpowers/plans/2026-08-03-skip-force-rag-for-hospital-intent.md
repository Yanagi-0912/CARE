# Skip Force RAG for Hospital Intent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 「我要看醫院」第一輪不再被 force RAG；改強制 `request_location_quick_reply`。

**Architecture:** Intent heuristic on latest human text；分流 force location vs force RAG。

**Tech Stack:** Python 3.12, pytest

## Global Constraints

- Prod evidence rid=d0d12b45: first agent_decide already `force_rag=True` for「我要看醫院」
- Previous fix (after location ToolMessage) insufficient for this path
- Named lookup cues (在哪/地址) must NOT force location
- Health symptom force RAG unchanged
- Workdir: `/Users/jamessu/Desktop/computersciencehomework/CARE`
- OpenSpec: `openspec/changes/skip-force-rag-for-hospital-intent/`

---

### Task 1: Intent heuristic + force location

**Files:** `app/services/agent/utils/nodes.py`, `tests/unit/services/agent/test_force_rag.py`, openspec tasks.md

- [ ] TDD then implement `_is_nearby_facility_intent`
- [ ] If facility intent + no tool_calls + not already location tools → inject `request_location_quick_reply` (log `force_location=True`)
- [ ] Force RAG also requires `not _is_nearby_facility_intent(user_text)`
- [ ] Tests for 我要看醫院 / 六隻腳趾 / 台大醫院在哪
- [ ] Commit `fix(agent): force location instead of RAG for hospital-seeking intents`
