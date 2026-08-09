## Why

使用者（含電腦版 LINE／外開）常問「官網怎麼開」「怎麼進 LIFF」，目前沒有穩定入口卡片；模型若只貼網址又不符合 Flex／原樣輸出慣例。需要一個可控的 tool，回傳官方入口 Flex Message。

## What Changes

- 新增 Agent tool `open_official_site`：回傳 LINE Flex JSON，含單一入口 URI 按鈕；URL 優先取 `LIFF_URL`，未設定時退回 `PUBLIC_BASE_URL`。
- 純函式產生 Flex bubble（resources／flex 模組），URL 從 settings 注入，禁止寫死網域。
- System prompt：使用者要官網／網站／打開 LIFF 入口時優先呼叫此 tool；有 Flex JSON 須原樣輸出。
- 短關鍵字（官網、打開官網、官方網站、打開網站、LIFF 怎麼開等）可 force 此 tool，並禁止同輪 force RAG。
- 單元測試覆蓋 Flex 結構、tool 註冊、force 意圖。

## Capabilities

### New Capabilities

- `official-site-entry`：官網／LIFF 入口 Flex tool 與強制路由

### Modified Capabilities

- `agent-architecture`：工具集納入 `open_official_site`；官網意圖可 force 該 tool、不 force RAG
- `line-reply-rules`：官網 Flex 回覆須原樣輸出（與院所 Flex 同規則）

## Impact

- **程式**：`app/tools/`、`registry.py`、`prompt.py`、`nodes.py`、flex 產生器、DI／config（沿用 `LIFF_URL`、`PUBLIC_BASE_URL`）
- **測試**：`tests/unit/tools/`、`tests/unit/services/agent/`
- **API**：無新 HTTP route；行為經 LINE Agent
- **部署**：無需新 env（既有 `LIFF_URL`、`PUBLIC_BASE_URL`）；若 `PUBLIC_BASE_URL` 空則僅顯示 LIFF 按鈕或降級純文字說明
- **歸檔順序（硬性）**：`crag-web-fallback` MUST 先歸檔。兩者都 MODIFY `agent-architecture` 的「代理可用工具集」，而 MODIFIED 是整塊取代；本 change 的 delta 已含 `crag-web-fallback` 的工具清單並額外加入 `open_official_site`，前提是它先落地。順序顛倒會讓 `open_official_site` 從主規格消失
