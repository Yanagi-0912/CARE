## Why

`voice-rate-and-multilingual-tts` 已讓語音跟隨使用者語言並開放三檔語速，但**音色仍寫死在後端**：`VOICE_BY_LANGUAGE`（`tts_service.py:37-45`）每個語言各綁一個 voice，使用者沒有任何入口可以更換。

六個預設全部是女聲。實測 `edge_tts.list_voices()`，六種支援語言**各自都恰好有一個男聲**可用（zh-TW YunJhe、en Andrew、ja Keita、th Niwat、vi NamMinh、id Ardi），因此「女聲／男聲」是唯一能在所有語言下一致成立的選擇軸。

## What Changes

- 新增設定欄位 `settings.voice_gender`（`female | male`，預設 `female`，維持現行行為）。
- `VOICE_BY_LANGUAGE` 由「語言 → voice」改為「語言 → 性別 → voice」兩層查表；未知語言 fallback `zh-TW`，未知性別 fallback `female`。
- 合成路徑多帶一個參數：`TTSService.synthesize(..., voice_gender=...)`，由 `message_handler` 自 profile settings 讀取後經 `reply()` 傳入。
- LIFF 設定頁的語音區塊新增一組兩選一的音色 `ToggleGroup`（女聲／男聲）。
- **非範圍**：開放選擇特定 voice 名稱（例如英文的 17 種）—— voice 名稱綁定語言，使用者切換介面語言後既有選擇即失效，需要額外的失效處理與 per-language 清單維護，複雜度與本次的取捨不同；n8n 分支（未啟用）；gTTS 備援的音色（gTTS 不支援音色選擇）。

## Capabilities

### Modified Capabilities

- `voice-reply`：新增「音色可由使用者選擇」的需求；既有的語言跟隨、語速、失敗備援、生命週期等需求不變。

## Impact

- **程式（CARE）**：`app/services/line_messaging/reply/tts_service.py`、`reply/reply.py`、`handler/message_handler.py`、`app/models/user.py`
- **程式（CARE-LIFF）**：`src/lib/settings.ts`、`src/pages/Settings/index.tsx`、`src/api/settingsApi.ts`、`src/i18n/messages.ts`
- **API／route**：無新端點，沿用 `GET/PATCH /api/profiles/me/settings`，僅擴充 `UserSettings` 欄位
- **資料**：`settings.voice_gender` 由 pydantic default 補值，舊資料免 migration；預設 `female` 等同現行音色，既有使用者聽感不變
- **不受影響**：音檔格式與命名、`GET /tts/{filename}`、時長計算、過期清檔、edge-tts timeout 設定、n8n 分支
- **測試計畫**：`pytest tests/unit/services/line_messaging/ tests/unit/routers/ -q` 全綠；新測試一律以依賴注入傳入 fake engine（專案規則禁止 monkey patch）；前端 `npx vitest run` 全綠
