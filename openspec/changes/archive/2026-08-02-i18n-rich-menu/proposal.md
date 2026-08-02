## Why

六語 Rich Menu 圖已齊，但 setup 只上傳 zh-TW，且 LIFF 改語言只換介面文案、不會換 LINE 底部選單。需一次完成「多語選單建立／ID 保存／改語言時 link／熱區 label 在地化」，使用者切語言後 Rich Menu 才會跟著變。

## What Changes

- `build_rich_menu_areas(liff_url, language)`：六語 action `label`／`chatBarText` 在地化；圖檔對應 `resources/rich_menu_{lang}.png`。
- 擴充 `scripts/setup_rich_menu.py`：為 `zh-TW`／`en`／`id`／`vi`／`th`／`ja` 各建立並上傳 Rich Menu；將 `richMenuId` 寫入可載入的對照（JSON 檔＋印出 env 片段）；預設選單設為 zh-TW。
- 新增 Rich Menu link 服務：依 language 對 `userId` 呼叫 LINE `POST /user/{userId}/richmenu/{richMenuId}`。
- `PATCH /api/profiles/me/settings` 在 `language` 變更時觸發 link（失敗只記 log，不讓設定更新失敗）。
- 設定／`.env.example`：支援讀取多語 `richMenuId` 對照。
- 單元測試覆蓋：label 語系、language→menuId 解析、settings 更新觸發 link（DI mock）。

## Capabilities

### New Capabilities

- （無全新能力名；擴充既有 `rich-menu`。）

### Modified Capabilities

- `rich-menu`：新增多語選單資產／建立、使用者語言與 Rich Menu 連結、本地化熱區 label。

## Impact

- **程式**：`rich_menu_layout.py`、新 `rich_menu_service.py`（或同等）、`setup_rich_menu.py`、`user_profile_service`／router DI、`config.py`、`.env.example`
- **資源**：既有 `resources/rich_menu_*.png`；setup 產出 `resources/rich_menu_ids.json`（可本機產生後提交或注入 secret）
- **API**：`PATCH /api/profiles/me/settings` 副作用（link menu）；回應契約不變
- **部署**：跑多語 setup 後把 IDs 配到 runtime env／檔案；CARE-infra 後續可注入
- **測試**：`tests/unit/services/line_messaging/`、settings 相關 unit
