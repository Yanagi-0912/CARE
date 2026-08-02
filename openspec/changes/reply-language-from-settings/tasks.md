## 1. 語系核心與訊息表

- [x] 1.1 新增 `app/core/user_language.py`（SUPPORTED_LANGUAGES、normalize、ContextVar set/get/reset）與單元測試
- [x] 1.2 新增 `app/i18n/messages.py`（`t(key, language=None)` + 六語文案；含 RAG fail、fallback、位置、postback 狀態）與單元測試
- [x] 1.3 `fail_messages.rag_fail` 改走 `t(...)`；更新既有 fail_messages 測試

## 2. Agent prompt

- [x] 2.1 `prompt.py`：`build_system_prompt(language)`；規則 1／RAG 前綴／來源標題依語言
- [x] 2.2 `nodes.py` `agent_node`：自 profile 取語言並組 prompt；單元測試

## 3. LINE／工具呼叫點

- [ ] 3.1 `message_handler`：處理時 set_request_language；fallback 字串用 `t`；把 language 傳給 `reply`
- [ ] 3.2 `reply.py`：Quick Reply label 依 language
- [ ] 3.3 `medical_tools`／`medical_service`：分享位置／無院所字串用 `t`
- [ ] 3.4 `dispatcher`：錯誤與 postback 使用者可見字串用 `t`（從 profile 取語言）
- [ ] 3.5 Agent 最終回覆補來源段落：比對本地化 sources heading（勿只認繁中）

## 4. 驗證

- [ ] 4.1 跑相關單元測試全綠
- [ ] 4.2 勾選本 tasks；必要時同步 superpowers plan checkboxes
