# Skip Force RAG After Location Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 已呼叫位置／院所工具後，不再 `force_rag` 注入 `get_rag_answer`。

**Architecture:** 在 `agent_node` force 條件加上 `_already_used_location_tools`。

**Tech Stack:** Python 3.12, pytest

## Global Constraints

- Prod evidence: after `request_location_quick_reply`, next step `force_rag=True`
- Location tool names: `request_location_quick_reply`, `find_nearby_hospitals`, `lookup_medical_facility`
- Keep force RAG for health Qs with no prior location/RAG tools
- Workdir: `/Users/jamessu/Desktop/computersciencehomework/CARE`
- OpenSpec: `openspec/changes/skip-force-rag-after-location-tools/`

---

### Task 1: Helper + force condition + tests

**Files:** `app/services/agent/utils/nodes.py`, `tests/unit/services/agent/test_force_rag.py`, openspec tasks.md

- [ ] TDD: no force when ToolMessage name=request_location_quick_reply present
- [ ] Implement helper + condition
- [ ] Existing force tests still pass
- [ ] Commit `fix(agent): do not force RAG after location/hospital tools`
