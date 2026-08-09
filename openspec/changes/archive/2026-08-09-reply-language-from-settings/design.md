## Context

- LIFF／Mongo 已存 `settings.language`：`zh-TW | en | id | vi | th | ja`。
- Rich Menu 用 `RICH_MENU_LANGUAGES` + `normalize_rich_menu_language`。
- Webhook 已 `get_user_profile`，但 Agent／工具／fallback 未讀 language。
- TTS／n8n 僅中文路徑 → 本 change **不做 TTS**。

## Goals / Non-Goals

**Goals:**

1. LLM 回覆語言跟隨設定（下一則訊息即生效）。
2. 列管固定字串六語（見下方 Message Keys）。
3. 未知／缺省語言 fallback `zh-TW`。
4. 請求處理期間工具層可取得目前語言（ContextVar 或同等），避免改每個 tool 簽名傳到 RAG 深處時爆炸。

**Non-Goals:**

- TTS、RAG 內部 `RAG_PROMPT`／query rewriter 多語、Flex 卡片全文翻譯、後端 validate PATCH language enum（可選小修但不阻塞）。

## Decisions

1. **語系常數**  
   抽出 `app/core/user_language.py`：`SUPPORTED_LANGUAGES` 與 Rich Menu 相同；`normalize_user_language(lang) -> str`。`rich_menu_layout` 可改 re-export／呼叫此函式，避免兩份清單漂移（若改動面過大，至少 reply-i18n 與 Rich Menu 共用同一 tuple）。

2. **請求語言 ContextVar**  
   `set_request_language`／`get_request_language`。在 `LineMessageHandler`（及必要的 dispatcher 路徑）於處理開始時從 profile settings 設定；結束時 reset。工具與 `rag_fail()` 預設讀 ContextVar。

3. **System prompt**  
   `build_system_prompt(language: str) -> str`：規則 1 改為「必須使用 {language_name} 回覆」；RAG 前綴／參考來源標題用該語言對應字串（訊息表 keys）。`agent_node` 組 prompt 時呼叫。

4. **訊息表**  
   `app/i18n/messages.py`：`t(key, language=None) -> str`，缺 key／缺語系 fallback zh-TW。Keys（最少）：
   - `rag.fail.KB_EMPTY` / `WEB_EMPTY` / `WEB_ERROR` / `MODEL_REFUSE`
   - `agent.rag_prefix`（原「以下為 RAG 回應：」）
   - `agent.sources_heading`（原「參考資料來源」）
   - `line.fallback_ununderstood`
   - `line.fallback_process_error`
   - `location.share_prompt`（工具請分享位置）
   - `location.share_qr_label`（Quick Reply）
   - `location.no_facility`
   - 既有 postback 使用者可見字串（語音開關確認等，若硬編碼在 dispatcher）

5. **傳遞**  
   - `LineReplier.reply(..., language=...)` 用於 QR label。  
   - `fail_messages.rag_fail(code, language=None)` → `t(...)`。  
   - 不強制改 RAG answer LLM prompt（non-goal）；fail 文案與 agent 轉述語言仍會對齊。

6. **OpenSpec**  
   同步改 `line-reply-rules`：語言依設定；純文字／無 Markdown 維持。

## Risks / Trade-offs

- [知識庫內容多為繁中] → LLM 以目標語言「說明」工具結果即可；不翻譯 KB。
- [ContextVar 漏設] → fallback zh-TW，行為與今日相同。
- [六語翻譯品質] → 先提供正確可讀翻譯；後續可調 copy。

## Migration Plan

1. 部署後使用者改 LIFF 語言 → 下一則 LINE 訊息即用新語言。
2. 無需 DB migration。

## Open Questions

- 無（範圍 B 已確認；TTS 排除）。
