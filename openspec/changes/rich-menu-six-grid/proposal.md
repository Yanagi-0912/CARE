## Why

現有 Rich Menu 是四格素色原型（家庭＋醫院＋語音開／關兩格），與已定案的六格產品選單不符；語音佔兩格浪費空間，且腳本仍上傳舊圖。需一次對齊圖檔、熱區、LIFF deep link，以及語音「一鍵切換」行為，作為後續多語 Rich Menu 的基礎。

## What Changes

- 將新六格圖歸檔為 `resources/rich_menu_zh-TW.png`（取代腳本對 `rich_menu.jpg` 的依賴）。
- 更新 `scripts/setup_rich_menu.py`：1200×810、六格熱區（各 400×405），對應家庭中心／用藥／附近醫院／我的家人／語音 toggle／設定。
- LIFF URI 以 `LIFF_URL` 為基底加上路徑（`/`、`/family`、`/settings` 等），讓選單能開到正確頁。
- 語音 postback 改為可省略 `enabled`：讀目前 `voice_reply_enabled` 後反轉；仍相容舊的 `enabled=true|false`。
- 本階段**不做**多語 Rich Menu 自動切換（另 change）；圖檔命名預留 `zh-TW`。

## Capabilities

### New Capabilities

- `rich-menu`: 定義六格 Rich Menu 版面、熱區行為、圖檔命名與 setup 腳本契約。

### Modified Capabilities

- （無既有 spec 涵蓋語音 postback；語音 toggle 行為納入 `rich-menu` 規格。）

## Impact

- **程式**：`scripts/setup_rich_menu.py`、`app/services/line_messaging/dispatcher/dispatcher.py`、相關單元測試
- **資源**：`resources/rich_menu_zh-TW.png`（由新 PNG 重新命名）；舊 `rich_menu.jpg` 可保留但不再被腳本使用
- **LIFF**：URI 指向 CARE-LIFF 既有路由（`/`、`/family`、`/settings`）；用藥頁尚未獨立時暫連 `/family`
- **API／route**：無新 HTTP route；僅 LINE postback 行為擴充
- **測試**：更新／新增 dispatcher 語音 toggle 測試；pytest 相關用例全綠
- **部署**：需在有 LINE token 的環境重跑 setup 腳本以上傳新選單（本 change 不自動打 LINE API）
