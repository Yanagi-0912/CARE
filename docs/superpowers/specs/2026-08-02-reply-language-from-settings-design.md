# Design: Reply language from settings

See OpenSpec change `openspec/changes/reply-language-from-settings/`（proposal／design／specs／tasks）為契約來源。

**Scope B：** Agent LLM 回覆 + 系統固定字串多語；**不含 TTS**。

**Approach：** `normalize_user_language` + request `ContextVar` + `t(key, lang)`；`build_system_prompt(lang)`；handler／tools／fail_messages／reply／dispatcher 接線。
