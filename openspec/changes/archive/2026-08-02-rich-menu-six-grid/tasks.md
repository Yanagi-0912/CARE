## 1. Asset

- [x] 1.1 將 `resources/LINE Rich Menu 1200x810.png` 重新命名為 `resources/rich_menu_zh-TW.png`

## 2. Voice toggle

- [x] 2.1 在 `tests/unit/services/line_messaging/test_event_handler.py` 新增「省略 enabled 時反轉」用例（DI mock，不用 monkey patch）
- [x] 2.2 更新 `dispatcher.py`：`toggle_voice_reply` 在無 `enabled` 時讀 profile 並反轉；保留顯式 `enabled`
- [x] 2.3 抽出 `rich_menu_layout` 並補單元測試

## 3. Setup script

- [x] 3.1 改寫 `scripts/setup_rich_menu.py` 為六格熱區、LIFF path URI、語音 toggle postback、上傳 `rich_menu_zh-TW.png`

## 4. Verify

- [x] 4.1 執行相關 pytest（至少 `tests/unit/services/line_messaging/test_event_handler.py`）確認通過
