## 0. 前置驗證

- [x] 0.1 `pip install edge-tts` 後跑 `edge-tts --list-voices`，核對並修正 `VOICE_BY_LANGUAGE` 六個 voice 名稱（泰／越／印尼務必確認）
- [ ] 0.2 六語各實聽一次，校準 `RATE_PERCENT` 的 slow／normal／fast 常數（待人耳驗收：rate 三檔經實測有效且差異明顯，但六語實聽校準尚未完成）
- [ ] 0.3 `kubectl get secret care-backend-secret -o jsonpath='{.data}'` 確認 `N8N_TTS_WEBHOOK_URL` 確實未設（決定本地分支是否真的是線上路徑）（需 kubectl 叢集權限，尚未執行）

## 1. 合成引擎

- [x] 1.1 `requirements.txt` 加入 `edge-tts`（`aiohttp` 已存在，不需再加）
- [x] 1.2 `tts_service.py`：新增 `VOICE_BY_LANGUAGE`、`RATE_PERCENT`，未知語言 fallback `zh-TW`
- [x] 1.3 `tts_service.py`：`synthesize()` 改為 `async def synthesize(text, language="zh-TW", voice_rate="normal")`，本地分支改用 `edge_tts.Communicate(...).stream()`
- [x] 1.4 `tts_service.py`：edge-tts 失敗時 fallback 至 gTTS；兩者皆失敗才拋出（由呼叫端吞例外只回文字）
- [x] 1.5 `tts_service.py`：n8n 分支以 `asyncio.to_thread` 包住既有 `requests.post`（不改寫為 aiohttp）
- [x] 1.6 `tts_service.py`：`available()` 的 gTTS 判斷改為 edge-tts／gTTS 任一可用
- [x] 1.7 依 `config.yaml` 規則改為可注入結構（`TTSService(engine=..., fallback_engine=...)`），於 `app/dependencies.py:281` 組裝；**測試不得使用 monkey patch**
- [x] 1.8 單元測試 `tests/unit/services/line_messaging/test_tts_service.py`：注入 fake engine，驗證語言→voice 對應、rate 字串格式（`"-25%"`／`"+0%"`）、未知語言 fallback、edge-tts 失敗改走 gTTS；既有 `test_synthesize_via_n8n_webhook` 與 `test_cleanup_expired_audio_files` 維持通過

## 2. 回覆路徑

- [x] 2.1 `reply.py`：`_append_tts_audio_message` 改為 async，新增 `language`／`voice_rate` 參數
- [x] 2.2 `reply.py:84`：改為 `await`，並把 `reply()` 已收到的 `language`（`reply.py:55`）與 `voice_rate` 傳下去（移除寫死的 `locale="zh-TW"`）
- [x] 2.3 `message_handler.py`：自 profile settings 取 `voice_rate`，與既有 `voice_reply_enabled` 一併傳入 `reply()`
- [x] 2.4 單元測試 `tests/unit/services/line_messaging/test_reply.py`：注入 fake TTS 驗證「語言正確往下傳」「Flex 回覆不觸發 TTS」「合成失敗仍回文字且不拋例外」

## 3. 設定欄位

- [x] 3.1 `app/models/user.py`：`UserSettings` 加 `voice_rate: Literal["slow","normal","fast"] = "normal"`；`UserSettingsUpdate` 加對應 `Optional`
- [x] 3.2 確認 `user_profile_service.update_user_settings` 與 repository 泛用 `settings.*` 寫入無需修改（僅補測試）
- [x] 3.3 單元測試：`tests/unit/routers/`（或既有 settings 測試路徑）驗證 PATCH `voice_rate` 可寫入、缺欄位舊資料讀取時補預設 `normal`、非法值回 422

## 4. LIFF 設定頁

- [x] 4.1 `CARE-LIFF/src/api/settingsApi.ts`：`ApiUserSettings` 加 `voice_rate`
- [x] 4.2 `CARE-LIFF/src/pages/Settings/index.tsx`：新增「語音」section —— 語音回覆開關（加入 `toggleFieldMap`，補上目前缺漏的 UI）＋ 語速三檔按鈕（沿用字體大小那組 `:180-196` 的樣式與 `aria-pressed` 結構）
- [x] 4.3 `defaultSettings` 與 localStorage fallback 加上語速預設值
- [x] 4.4 `CARE-LIFF/src/i18n`：六語新增 `settings.voiceTitle`／`voiceDesc`／`voiceReplyToggle`／`voiceRateSlow`／`voiceRateNormal`／`voiceRateFast`
- [x] 4.5 `CARE-LIFF/src/tests/settings.test.tsx`：補語速選擇與語音開關的 case

## 5. 周邊

- [x] 5.1 `scripts/diagnose_tts.py`：`synthesize` 改 async 後以 `asyncio.run` 呼叫；輸出訊息由 "gTTS available" 改為顯示實際引擎
- [x] 5.2 `.env.example`／`config.py`：若引入新的環境變數（如預設語速覆寫）需同步補上，否則不動

## 6. 驗證

- [x] 6.1 `pytest tests/unit/services/line_messaging/test_tts_service.py tests/unit/services/line_messaging/test_reply.py tests/unit/routers/test_tts.py -q` 全綠
- [x] 6.2 `./init.sh` 全綠（Definition of Done）（實際執行時 `./init.sh` 因缺可執行位而 shell-level 失敗，改用 `bash init.sh` 執行後全綠；未修改 `init.sh` 本身或 chmod）
- [ ] 6.3 手動：六種語言各發一則訊息，確認發音語言正確、三檔語速有感差異（待人耳驗收，需真實 LINE 帳號）
- [ ] 6.4 手動：LIFF 設定頁調整後，下一則 LINE 訊息即生效（待人耳驗收，需真實 LINE 帳號）
- [x] 6.5 勾選本 tasks 並建立清楚的 git commit／PR（繁體中文描述）
