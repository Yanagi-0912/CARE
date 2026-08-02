## Context

Rich Menu 由 `scripts/setup_rich_menu.py` 建立並上傳圖檔。現況為左大右三（語音開／關分兩格），圖為 `resources/rich_menu.jpg`。產品已定案六格均等選單，新圖在 `resources/LINE Rich Menu 1200x810.png`。CARE-LIFF 路由含 `/`、`/family`、`/settings`；尚無獨立用藥頁。LIFF i18n 支援 `zh-TW | en | id | vi | th | ja`，多語 Rich Menu 換圖留待後續。

## Goals / Non-Goals

**Goals:**

- 六格熱區與新圖對齊，腳本可一鍵建立並設為預設選單。
- LIFF 相關格以 `LIFF_URL + path` 開啟對應頁。
- 語音一鍵 postback：省略 `enabled` 時讀檔反轉；保留 `enabled=true|false` 相容。
- 圖檔標準命名 `rich_menu_zh-TW.png`。

**Non-Goals:**

- 依語言自動 link 不同 Rich Menu（後續 change）。
- 在 CARE-LIFF 新增獨立用藥頁。
- 語音開／關兩種不同選單圖（避免語言×狀態組合爆炸）。

## Decisions

1. **熱區幾何**：1200×810，每格 400×405，列優先：
   - (0,0) 家庭中心 → URI `/`
   - (400,0) 用藥提醒 → URI `/family`（暫代，無 `/medications`）
   - (800,0) 附近醫院 → `location`
   - (0,405) 我的家人 → URI `/family`
   - (400,405) 語音回覆 → `postback` `action=toggle_voice_reply`（無 enabled）
   - (800,405) 設定 → URI `/settings`

2. **LIFF URI 組合**：`{LIFF_URL.rstrip('/')}{path}`。若 `LIFF_URL` 已是 `https://liff.line.me/{id}`，路徑會變成 `.../{id}/family`，需 Endpoint URL 支援 path（CARE-LIFF 現況適用）。

3. **語音 toggle**：dispatcher 在缺少 `enabled` 時呼叫 `get_user_profile`，沿用 message_handler 相同優先序解析 `settings.voice_reply_enabled`／頂層欄位，預設 `False`（與 `UserSettings` 一致），再 `update_voice_reply_enabled(user_id, not current)`。

4. **圖檔**：`mv`／rename 為 `resources/rich_menu_zh-TW.png`；腳本只讀此檔；Content-Type `image/png`。

5. **舊開／關 postback**：仍接受 `enabled=true|false`，不刪除行為，避免舊選單殘留時失效。

## Risks / Trade-offs

- [用藥與家人同連 `/family`] → 接受暫代；之後有獨立頁再改 setup 腳本 path。
- [重跑 setup 會建新 Rich Menu、舊的仍留在 LINE 後台] → 文件註明可手動刪舊選單；必要時加 list/delete 另議。
- [使用者無 profile 時 toggle] → 視為目前關閉，開啟並寫入；若 update 失敗仍回覆錯誤語意需保守處理（維持現有 update 回傳模式）。

## Migration Plan

1. Merge 程式與圖檔。
2. 在有 `.env` LINE 憑證的環境執行 `python scripts/setup_rich_menu.py`。
3. LINE 客戶端重開聊天室驗證六格與語音切換。
4. Rollback：重新上傳舊四格選單（若仍保留 `rich_menu.jpg`）或於 LINE 後台切回舊 richMenuId。

## Open Questions

- 用藥獨立路由名稱（未來）：建議 `/medications` 與 family 子頁對齊時再改腳本。
