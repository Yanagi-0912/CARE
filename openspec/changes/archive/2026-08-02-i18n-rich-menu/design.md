## Context

LIFF `SupportedLanguage`：`zh-TW | en | id | vi | th | ja`。圖檔已命名 `resources/rich_menu_{lang}.png`。既有 `rich_menu_layout.build_rich_menu_areas` 僅繁中 label；`setup_rich_menu.py` 只處理 zh-TW。設定經 `PATCH /api/profiles/me/settings` 更新 `language`。

## Goals / Non-Goals

**Goals:**

- 六語各一 Rich Menu（同熱區幾何、本地化 label、對應 PNG）。
- Runtime 可依 language 取得 richMenuId 並 link 到使用者。
- 更新 settings.language 時自動 link；未知語言 fallback zh-TW。
- Setup 腳本可重跑並輸出 ID 對照。

**Non-Goals:**

- 語音 on/off 兩套選單圖。
- CARE-LIFF 前端改動（已會呼叫 settings API）。
- 自動刪除 LINE 後台舊 Rich Menu。

## Decisions

1. **支援語系列表**：常數 `RICH_MENU_LANGUAGES = ("zh-TW", "en", "id", "vi", "th", "ja")`，與 LIFF 對齊。

2. **ID 存放**：setup 寫入 `resources/rich_menu_ids.json`（形如 `{"zh-TW":"richmenu-xxx",...}`）。Runtime 優先讀 env `RICH_MENU_IDS_JSON`（整段 JSON 字串），否則讀該檔。方便本機檔案與 CI secret 兩種部署。

3. **服務切面**：新增 `RichMenuService`（token via `LineTokenManager.get_token`）：
   - `resolve_menu_id(language) -> str | None`
   - `link_user_menu(user_id, language) -> bool`（HTTP link；失敗 return False + warning log）

4. **觸發點**：`UserProfileService.update_user_settings` 若 `language` 在 changed_fields 中，呼叫 `RichMenuService.link_user_menu`（可選注入；未注入則跳過）。不因 link 失敗 rollback DB。

5. **Label 在地化**：`AREA_LABELS[lang][slot]` + `CHAT_BAR_TEXT[lang]`；`build_rich_menu_areas(liff_url, language="zh-TW")`。

6. **預設選單**：setup 最後以 zh-TW 的 id 呼叫 `user/all/richmenu/{id}`。

## Risks / Trade-offs

- [LINE API rate／重複建立] → setup 可接受多份舊選單；文件註明可手動清。
- [IDs 未配置時 link no-op] → log warning；設定仍成功。
- [同步 HTTP 在 async route] → 以 `asyncio.to_thread` 包 requests 或沿用專案既有 sync-in-async 模式（token_manager 為 sync）。

## Migration Plan

1. Merge 程式。
2. 本機執行 `python scripts/setup_rich_menu.py` 產生六語選單與 `rich_menu_ids.json`。
3. 將 JSON 內容設為 deploy 的 `RICH_MENU_IDS_JSON`（或提交 json 若環境允許）。
4. 重啟 backend；LIFF 切語言驗證選單文字／圖。

## Open Questions

- 無（語系清單與 LIFF 已對齊）。
