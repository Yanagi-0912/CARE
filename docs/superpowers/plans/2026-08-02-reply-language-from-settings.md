# Reply Language From Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** LINE Agent 回覆與系統固定字串跟隨 `settings.language`（六語）；不含 TTS／RAG 內部 LLM prompt。

**Architecture:** `user_language` normalize + request ContextVar；`i18n.messages.t` 訊息表；`build_system_prompt(lang)`；handler／dispatcher／tools／fail_messages／reply 改讀語言。

**Tech Stack:** Python 3.12, pytest, 既有 LangGraph agent / LINE handler

## Global Constraints

- 語系：`zh-TW | en | id | vi | th | ja`；未知 → `zh-TW`
- 不做 TTS、不改 RAG 生成／改寫 prompt（fail 文案與來源標題要做）
- 測試 patch 模組路徑；勿 monkey-patch DI 全域服務實例
- 面向使用者字串放訊息表，勿散落硬編碼新語言
- Commit 訊息用繁中或英文短句皆可；每個 task 結束可 commit
- Workdir：`/Users/jamessu/Desktop/computersciencehomework/CARE`，branch `feat/reply-language-from-settings`
- OpenSpec：`openspec/changes/reply-language-from-settings/`

---

## File map

| File | Responsibility |
|------|----------------|
| `app/core/user_language.py` | SUPPORTED_LANGUAGES, normalize, ContextVar |
| `app/i18n/__init__.py` | re-export `t` |
| `app/i18n/messages.py` | key → 六語文案 |
| `app/services/rag/fail_messages.py` | rag_fail → t() |
| `app/services/agent/prompt.py` | build_system_prompt |
| `app/services/agent/utils/nodes.py` | 取 language、組 prompt |
| `app/services/agent/agent.py` | 補來源段落認本地化 heading |
| `app/services/line_messaging/handler/message_handler.py` | set language + fallbacks |
| `app/services/line_messaging/reply/reply.py` | QR label |
| `app/services/line_messaging/dispatcher/dispatcher.py` | postback／error 字串 |
| `app/tools/medical_tools.py` | share prompt |
| `app/services/medical/medical_service.py` | no facility |
| `app/services/rag/answer_service.py`（若組裝來源標題） | sources heading 用 t() |

---

### Task 1: user_language + messages + fail_messages

**Files:**
- Create: `app/core/user_language.py`, `app/i18n/__init__.py`, `app/i18n/messages.py`
- Create: `tests/unit/core/test_user_language.py`, `tests/unit/i18n/test_messages.py`
- Modify: `app/services/rag/fail_messages.py`, `tests/unit/services/rag/test_fail_messages.py`
- Modify: `openspec/changes/reply-language-from-settings/tasks.md` (check 1.x)

- [ ] **Step 1: 寫 failing tests** — normalize 已知／未知；ContextVar；`t("rag.fail.KB_EMPTY", "en")` 非繁中；`rag_fail(KB_EMPTY)` 在 set en 後為英文

- [ ] **Step 2: 實作** — 訊息 keys 至少：
  - `rag.fail.KB_EMPTY|WEB_EMPTY|WEB_ERROR|MODEL_REFUSE`
  - `agent.rag_prefix`, `agent.sources_heading`（含或不含冒號：統一 `t` 回傳含結尾標點的完整標題，繁中 `參考資料來源：`）
  - `line.fallback_ununderstood`, `line.fallback_process_error`
  - `location.share_prompt`, `location.share_qr_label`, `location.no_facility`
  - `meds.recorded`, `meds.already_recorded`
  - `voice.enabled`, `voice.disabled`, `voice.need_login`

- [ ] **Step 3: pytest** `tests/unit/core/test_user_language.py tests/unit/i18n/test_messages.py tests/unit/services/rag/test_fail_messages.py -q`

- [ ] **Step 4: Commit** `feat(i18n): user language context and message catalog`

---

### Task 2: build_system_prompt + agent_node

**Files:**
- Modify: `app/services/agent/prompt.py`, `app/services/agent/utils/nodes.py`
- Modify: `tests/unit/services/agent/test_prompt.py`
- Create/Modify: agent node language test
- Check openspec tasks 2.x

- [ ] **Step 1: Failing test** — `build_system_prompt("en")` 不含「必須只使用繁體中文」；含英文回覆指示；`build_system_prompt("zh-TW")` 仍要求繁中

- [ ] **Step 2: 實作** — `SYSTEM_PROMPT` 可保留為 `build_system_prompt("zh-TW")` 相容 alias；nodes 從 `user_profile.get("settings", {}).get("language")` normalize 後呼叫

- [ ] **Step 3: pytest** agent prompt／nodes 相關

- [ ] **Step 4: Commit** `feat(agent): system prompt follows user language`

---

### Task 3: LINE handler, reply, tools, dispatcher, sources append

**Files:** listed in File map (handler, reply, dispatcher, medical_*, agent.py sources, answer_service if needed)

- [ ] **Step 1: Failing tests** — reply QR label ja；message_handler fallback en（可 mock）；medical share prompt via ContextVar；agent sources append 認 `t("agent.sources_heading", lang)`

- [ ] **Step 2: 實作** — handler 進出 set/reset ContextVar；`reply(..., language=)`；dispatcher 取 profile language；sources 後置處理用 heading 變數

- [ ] **Step 3: pytest** `tests/unit/services/line_messaging/ tests/unit/services/agent/ tests/unit/services/medical/ -q`（存在者）

- [ ] **Step 4: Commit** `feat(i18n): wire language into LINE replies and tools`

---

### Task 4: OpenSpec checkboxes + full unit verify

- [ ] 勾選 `tasks.md` 全部
- [ ] `pytest tests/unit -q --tb=line`（或專案慣用）確認無回歸
- [ ] Commit `docs(openspec): complete reply-language-from-settings tasks`
