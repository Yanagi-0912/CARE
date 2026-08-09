## Context

- `VOICE_BY_LANGUAGE`（`tts_service.py:37-45`）目前是單層 `語言 → voice` 常數，六個值全為女聲。
- `_synthesize_bytes`（`tts_service.py:150-155`）已負責 normalize 語言、查 voice、把 `voice_rate` 轉成 `f"{percent:+d}%"`，是本次唯一需要改的查表點。
- 語言與語速的傳遞管線（`message_handler` → `reply()` → `_append_tts_audio_message` → `synthesize`）已於前一個 change 打通，本次只是沿同一條路多帶一個參數。
- 設定讀寫為泛用實作：`update_user_settings`（`user_profile_service.py:51`）以 `model_dump(exclude_unset, exclude_none)` 取差異、repository 以 `settings.<key>` dot-notation 寫入、`get_user_settings` 以 `UserSettings(**raw)` 補預設 —— **新增欄位不需改動 service 與 repository**。
- 前端的語音區塊、`toggleFieldMap`、`ToggleGroup` 三檔語速樣式皆已存在，可直接沿用。

### 實測資料（`edge_tts.list_voices()`）

| 語言 | 女聲（現行預設） | 男聲 |
|---|---|---|
| zh-TW | HsiaoChenNeural | YunJheNeural |
| en | AriaNeural | AndrewNeural |
| ja | NanamiNeural | KeitaNeural |
| th | PremwadeeNeural | NiwatNeural |
| vi | HoaiMyNeural | NamMinhNeural |
| id | GadisNeural | ArdiNeural |

六種語言各有至少一個男聲，`Gender` 欄位取自 `list_voices()` 而非由名稱推測。

## Goals / Non-Goals

**Goals:**

1. 使用者可自行選擇女聲或男聲，下一則回覆即生效。
2. 預設 `female` 與現行六個 voice 完全一致，既有使用者聽感不變。
3. 未知語言與未知性別皆有明確 fallback，不得因設定值異常而中斷回覆。

**Non-Goals:**

- 開放選擇特定 voice 名稱（見下方 Decision 1 的理由）。
- gTTS 備援的音色 —— gTTS 不支援音色選擇，備援時性別設定會被忽略，這是已知且可接受的降級。
- n8n 分支（目前未啟用）。

## Decisions

1. **以「性別」而非「voice 名稱」作為使用者可見的選項**
   voice 名稱綁定語言（`zh-TW-YunJheNeural` 只對中文有效）。若讓使用者直接選 voice，介面語言一改，既有選擇立即失效，必須額外設計失效偵測與 per-language 選單，且每種語言的可選數量差異極大（en 有 17 種、th/vi/id 各 2 種），UI 難以一致。
   性別是語言無關的軸，六種語言皆成立，兩層查表即可涵蓋，且未來要擴充成「選特定 voice」時，這層抽象不會擋路。

2. **兩層查表，維持單一常數來源**
   `VOICE_BY_LANGUAGE: dict[str, dict[str, str]]`，形如 `{"zh-TW": {"female": ..., "male": ...}, ...}`。
   查表順序：先 normalize 語言（沿用 `SUPPORTED_LANGUAGES` 判斷，未知 → `DEFAULT_USER_LANGUAGE`），再取性別（未知 → `DEFAULT_VOICE_GENDER = "female"`）。
   保留 `.get(..., default)` 形式的安全網，避免未來新增第七種語言時在回覆路徑上拋 `KeyError`。

3. **預設值三處必須一致**：後端 `UserSettings.voice_gender` 預設 `female`、`tts_service.DEFAULT_VOICE_GENDER` 為 `"female"`、前端 `defaultSettings.voiceGender` 為 `'female'`。任何一處不一致都會讓「沒動過設定的使用者」在不同入口看到不同狀態 —— 前一個 change 的 `voice_reply_enabled` 就是這樣出過事。

4. **前端沿用既有 `ToggleGroup` 模式**
   與語速三檔相同的元件與樣式，只是兩顆。送出的值必須是 `female`／`male` 字串本身，而非顯示標籤。

## Risks / Trade-offs

- **gTTS 備援不套用性別** —— 使用者選了男聲、edge-tts 失敗時會聽到 gTTS 的預設音色。屬引擎能力限制，非實作缺陷；與既有的「備援不套用語速」是同一類降級。
- **英文只暴露一種男聲** —— en 有 9 種男聲，本設計只取 `AndrewNeural`。若之後要開放更多，屬 Non-Goals 提到的後續議題。
- **音色品質未經人耳驗收** —— 與前一個 change 相同的天花板：可驗證「產出合法 mp3」，無法驗證「這個男聲唸醫療詞是否清楚」。男聲需與女聲一併實聽。

## Migration Plan

無資料 migration。`settings.voice_gender` 缺值時由 `UserSettings` default 補為 `female`，與現行行為完全相同。前端 `defaultSettings` 同步加預設值。

**部署順序**：與前一個 change 相同，**後端先**。前端將 `voice_gender` 型別為必填，後端先上線才不會拿到 `undefined`。

## Open Questions

- 男聲的醫療詞發音品質需與女聲一併實聽驗收（沿用前一個 change 未完成的人耳驗收項目）。
