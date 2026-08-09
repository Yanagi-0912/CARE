## Why

使用者可在 LIFF 設定切換 `settings.language`（六語系），Rich Menu 已跟隨，但 LINE Agent 回覆與系統固定字串仍硬編碼繁體中文，造成「介面語言」與「對話語言」不一致。

## What Changes

- Agent system prompt 依 `user_profile.settings.language` 動態指定回覆語言（不再寫死繁中）。
- 系統固定字串多語化（至少）：RAG fail messages、處理失敗／無法理解 fallback、請分享位置（工具文案＋Quick Reply label）、附近院所找不到文案、dispatcher／postback 常用提示。
- 共用語系 normalize（與 Rich Menu／LIFF 六語對齊；未知 → `zh-TW`）。
- 更新 `line-reply-rules`／`agent-architecture`：由「一律繁中」改為「依使用者語言設定」。
- **非範圍**：TTS locale／n8n 多語語音、RAG 內部生成 prompt／查詢改寫、Flex 院所卡完整多語、CARE-LIFF UI（已有 i18n）。

## Capabilities

### New Capabilities

- `reply-i18n`：使用者語言解析、訊息表、以及 LINE／工具固定字串依語言輸出。

### Modified Capabilities

- `line-reply-rules`：回覆語言改跟 `settings.language`；純文字／無 Markdown 規則不變；RAG 前綴與「參考資料來源」標題改為依語言。
- `agent-architecture`：system prompt 依語言組裝。

## Impact

- **程式**：`prompt.py`、`nodes.py`、`fail_messages.py`、`message_handler.py`、`dispatcher.py`、`reply.py`、`medical_tools.py`、`medical_service.py`；新增 `app/i18n/`（或同等模組）
- **API／route**：無新端點；讀既有 profile settings
- **測試計畫**：單元測試涵蓋 normalize、訊息表、prompt 語言規則、關鍵呼叫點；`pytest tests/unit -q` 相關路徑全綠
- **歸檔順序（硬性）**：`no-fabricated-rag-sources` MUST 先歸檔。兩者都 MODIFY `line-reply-rules` 的「保留參考資料來源」，而 MODIFIED 是整塊取代；本 change 的 delta 已把「工具輸出不含來源標題時不得捏造」一併寫入，前提是它先落地。順序顛倒會讓防捏造條文從主規格消失
