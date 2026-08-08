## Why

`settings.language` 六語系已全面生效（`reply-language-from-settings` 已完成），Agent 會用使用者語言回覆，但**語音仍只有中文**：`reply.py:221-224` 把 locale 寫死 `"zh-TW"`，`tts_service.py:53` 的 gTTS 語言又只有 `zh / en` 二分法。結果是日／泰／越／印尼語的回覆被用**英文發音**唸出來。

同時 gTTS 只提供 `slow: bool` 一個開關（實測六語僅慢 18–21%），使用者反映語速偏慢卻無法調整；LIFF 設定頁也還沒有任何語音相關控制項（`voice_reply_enabled` 已在 model／API／前端 type 中，但 `Settings/index.tsx` 未渲染，目前僅能由 Rich Menu 切換）。

TTS 多語在 `reply-language-from-settings/proposal.md:11` 被明確列為非範圍，本 change 即為其預定後續。

## What Changes

- 本地合成引擎改用 **edge-tts**（原生 asyncio、六語 neural voice、`rate` 連續調速），gTTS 降為 fallback。
- `TTSService.synthesize()` 介面由 `locale` 改為 `language` + `rate_percent`，並改為 **async**；`LineReplier` 把既有的 `language` 往下傳（目前已收到但未使用）。
- 新增設定欄位 `settings.voice_rate`（`slow | normal | fast`，預設 `normal`），比照 `font_size` 的三檔慣例。
- LIFF 設定頁新增「語音」區塊：語音回覆開關（補上目前缺漏的 UI）＋ 語速三檔選擇。
- 修正既有缺陷：同步 `synthesize()` 被 `async def reply` 直接呼叫，導致每次合成阻塞 event loop 數秒。
- **非範圍**：n8n workflow 與 `local-tts` 服務（本 change 不啟用該分支）、付費 TTS／發音辭典、台語語音、Flex 訊息附語音、`voice_reply_enabled` 兩處寫入路徑的統一（見 design.md 風險段）。

## Capabilities

### New Capabilities

- `voice-reply`：語音回覆的語言選擇、語速設定、合成引擎 fallback 與音檔生命週期契約。

### Modified Capabilities

- `rich-menu`：語音一鍵切換維持，但明確化「Rich Menu 與 LIFF 設定頁為同一份設定的兩個入口」。

## Impact

- **程式（CARE）**：`app/services/line_messaging/reply/tts_service.py`、`reply/reply.py`、`app/models/user.py`、`app/dependencies.py`、`scripts/diagnose_tts.py`、`requirements.txt`
- **程式（CARE-LIFF）**：`src/pages/Settings/index.tsx`、`src/api/settingsApi.ts`、`src/i18n`（六語字串）
- **API／route**：無新端點。沿用 `GET/PATCH /api/profiles/me/settings`（`upsert_users.py:54`、`:74`），僅擴充 `UserSettings` 欄位；`GET /tts/{filename}` 完全不變（edge-tts 同樣輸出 mp3）
- **資料**：`settings.voice_rate` 由 pydantic default 補值，舊資料免 migration
- **不受影響**：n8n workflow JSON、`CARE-n8n/`、`app/routers/tts/tts.py`、時長計算與過期清檔、`Dockerfile`（不需 ffmpeg）
- **測試計畫**：`pytest tests/unit/services/line_messaging/test_tts_service.py tests/unit/services/line_messaging/test_reply.py tests/unit/routers/test_tts.py -q` 全綠；新測試一律以依賴注入傳入 fake 合成器（不使用 monkey patch）；前端 `CARE-LIFF/src/tests/settings.test.tsx` 補語速選擇 case
