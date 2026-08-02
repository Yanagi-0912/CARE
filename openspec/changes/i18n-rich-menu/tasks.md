## 1. Localized layout

- [x] 1.1 擴充 `rich_menu_layout.py`：語系 label／chatBarText、`image_path_for_language`、`build_rich_menu_areas(..., language=)`
- [x] 1.2 新增／更新 `tests/unit/services/line_messaging/test_rich_menu_layout.py`（en labels、fallback）

## 2. Rich menu ID config + service

- [ ] 2.1 `config`／`.env.example`：`RICH_MENU_IDS_JSON`；loader 讀 env 或 `resources/rich_menu_ids.json`
- [ ] 2.2 新增 `RichMenuService`（resolve + link）；DI 於 `dependencies.py`
- [ ] 2.3 單元測試：resolve／link 成功與缺 ID（DI mock，不用 monkey patch）

## 3. Settings hook

- [ ] 3.1 `UserProfileService.update_user_settings` 在 language 變更時呼叫 link
- [ ] 3.2 測試：language 變更觸發 link；link 失敗仍回傳 settings

## 4. Setup script

- [ ] 4.1 改寫 `setup_rich_menu.py` 迴圈六語上傳並寫 `rich_menu_ids.json`；預設 zh-TW

## 5. Verify

- [ ] 5.1 跑相關 pytest 全綠
