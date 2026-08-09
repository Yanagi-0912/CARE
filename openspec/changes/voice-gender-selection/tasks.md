## 1. 後端合成層

- [x] 1.1 `tts_service.py`：`VOICE_BY_LANGUAGE` 改為「語言 → 性別 → voice」兩層查表，新增 `DEFAULT_VOICE_GENDER = "female"`，六個女聲維持現值不變
- [x] 1.2 `tts_service.py`：`synthesize()` 與 `_synthesize_bytes()` 新增 `voice_gender` 參數（預設 `"female"`）；查表時未知語言 fallback `DEFAULT_USER_LANGUAGE`、未知性別 fallback `DEFAULT_VOICE_GENDER`
- [x] 1.3 單元測試 `tests/unit/services/line_messaging/test_tts_service.py`：注入 fake engine，驗證六語 × 兩性別的 voice 對應正確、未知性別 fallback `female`、未知語言 fallback `zh-TW`、預設參數等同現行女聲；既有測試維持通過

## 2. 後端傳遞路徑

- [x] 2.1 `reply.py`：`reply()` 與 `_append_tts_audio_message()` 新增 `voice_gender` 參數（給預設值，使 `dispatcher.py`／`facility_detail_handler.py` 的既有呼叫端無須修改）
- [x] 2.2 `message_handler.py`：比照既有的 `_parse_voice_rate`，新增自 profile settings 讀取 `voice_gender` 的解析（缺值回 `"female"`），並傳入 `reply()`
- [x] 2.3 單元測試 `tests/unit/services/line_messaging/test_reply.py`：注入 fake TTS 驗證性別正確往下傳、缺值時為 `female`

## 3. 設定欄位

- [x] 3.1 `app/models/user.py`：`UserSettings` 加 `voice_gender: Literal["female","male"] = "female"`；`UserSettingsUpdate` 加對應 `Optional`，欄位置於 `voice_rate` 之後
- [x] 3.2 單元測試：PATCH `voice_gender` 可寫入、舊資料讀取補預設 `female`、非法值回 422（沿用 `tests/unit/routers/test_upsert_users.py` 與 `tests/unit/services/users/test_user_profile_service.py` 的既有風格與 fixture）

## 4. LIFF 設定頁

- [x] 4.1 `CARE-LIFF/src/api/settingsApi.ts`：`ApiUserSettings` 加 `voice_rate` 之後加 `voice_gender`
- [x] 4.2 `CARE-LIFF/src/lib/settings.ts`：`SettingsState` 與 `defaultSettings` 加 `voiceGender`（預設 `'female'`）
- [x] 4.3 `CARE-LIFF/src/pages/Settings/index.tsx`：語音區塊新增音色 `ToggleGroup`（女聲／男聲兩顆），沿用語速三檔的元件與樣式；掛載時的 API 覆蓋對應（`getUserSettings().then(...)`）必須一併加上新欄位
- [x] 4.4 `CARE-LIFF/src/i18n/messages.ts`：六語新增 `settings.voiceGenderLabel`／`voiceGenderFemale`／`voiceGenderMale`（越南文沿用該檔既有的無聲調 ASCII 慣例）
- [x] 4.5 `CARE-LIFF/src/tests/settings.test.tsx`：補音色選擇的 case（送出的值為 `female`／`male` 而非顯示標籤、預設為 `female`）

## 5. 驗證

- [x] 5.1 後端 `.venv/bin/python -m pytest tests/unit -q` 全綠（此 worktree 無 `.venv`，實際以主 checkout 的 `/Users/jamessu/Desktop/computersciencehomework/CARE/.venv/bin/python -m pytest tests/unit -q` 執行，1246 passed）
- [x] 5.2 前端 `npx vitest run`、`npm run build` 全綠，lint 問題數不增加（vitest 15 files／83 tests passed；build 成功；lint 19 problems／16 errors／3 warnings，與 Task 4 基準相同）
- [x] 5.3 手動：切換男聲後發一則訊息，確認音色實際改變（已由專案負責人實聽驗收，六語音色與語速皆確認可用）
- [x] 5.4 勾選本 tasks 並建立清楚的 git commit（繁體中文描述）
