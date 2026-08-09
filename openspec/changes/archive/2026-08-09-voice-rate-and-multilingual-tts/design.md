## Context

- 合成入口 `TTSService.synthesize()`（`tts_service.py:37-68`）是二選一分支：`N8N_TTS_WEBHOOK_URL` 有值走 n8n webhook，否則本地 gTTS。
- 實際部署走的是 gTTS：`N8N_TTS_WEBHOOK_URL` 不在 `CARE-infra` 的 `values.yaml` 或 `configmap-backend.yaml`，全 repo 搜 `N8N_TTS` 零命中。（`care-backend-secret` 由 CI／kubectl 建立、內容不在 repo，上線前需以 `kubectl get secret` 複驗。）
- n8n workflow（`resources/workflows/tts webhook.json`）的 route 只認 `zh` 與 `taiwanese`，其餘回 400；且它指向的 `local-tts:8300` 在 `CARE-n8n/docker-compose.yml` 中並不存在 → 該分支目前等同未實作。
- `LineReplier.reply()` 已收到 `language`（`reply.py:55`）供 Quick Reply label 使用，但未傳給 TTS。
- 設定讀寫鏈已完整：`UserSettings`（`user.py:26`）→ `update_user_settings`（`user_profile_service.py:51`）→ repository 泛用 `settings.*` 寫入（`user_profile_repository.py:64`），**新增欄位不需改動 service／repository**。

### 實測數據（專案 `.venv`，實際呼叫非查表）

gTTS 六語皆產出合法 mp3；`slow=True` 僅慢 18–21%（zh-TW 3.17s→3.84s、en 2.42s→2.86s、ja 3.55s→4.27s、th 3.41s→4.03s、vi 3.38s→4.08s、id 3.00s→3.60s）。
另：gTTS 語言 code 大小寫不敏感（`zh-tw` 可用），但**不接受 locale 形式**（`vi-VN`／`th-TH` 直接 `ValueError`），`nan`（台語）不支援。

## Goals / Non-Goals

**Goals:**

1. 語音語言跟隨 `settings.language`（六語系），未知 fallback `zh-TW`。
2. 使用者可自行調整語速（三檔），下一則訊息即生效。
3. 合成失敗永不阻斷回覆 —— 維持現有「吞例外、只回文字」行為，並多一層 gTTS fallback。
4. 移除 async 路徑上的同步阻塞。

**Non-Goals:**

- 啟用／修改 n8n TTS 分支與 `local-tts` 服務。
- 付費 TTS（Azure Speech／Google Cloud TTS）與發音辭典（藥名矯正）。
- 台語語音、Flex 訊息附語音、混語句子的多 voice 切換。

## Decisions

1. **語速用列舉而非數值**
   `voice_rate: Literal["slow", "normal", "fast"]`，預設 `normal`。理由：與既有 `font_size` 的三檔慣例一致，LIFF 可直接沿用「字體大小」那組三按鈕的樣式與 `aria-pressed` 結構；也避免使用者調出極端值。
   後端維護單一映射常數 `RATE_PERCENT = {"slow": -25, "normal": 0, "fast": +25}`，轉成 edge-tts 的 `rate="+0%"` 字串格式。
   *注意*：edge-tts 在 `+0%` 的基準語速本來就與 gTTS 不同，上線前應實聽並校準這三個常數；n8n 契約用的是 0.5–2.0 浮點，未來若要對接再加一層映射即可。

2. **edge-tts 放在 CARE 內，不做成 n8n 後的服務**
   走 n8n 需多兩跳（CARE → n8n → local-tts），且該 workflow 的 route 只認中文、多語照樣得改，等於白繞。放 CARE 內只動一個檔的本地分支。

3. **保留 gTTS 作為 fallback**
   edge-tts 與 gTTS 皆為非官方 wrapper（分別打微軟 Edge 與 Google Translate 端點），皆無 SLA。edge-tts 失敗時退回 gTTS（語言用同一份 `SUPPORTED_LANGUAGES` code，gTTS 亦全數支援），觀察穩定度後再決定是否移除。

4. **`synthesize()` 改為 async；n8n 分支用 `asyncio.to_thread` 包住**
   n8n 分支刻意**不**改寫成 aiohttp：既有 `requests.post` 契約與其測試維持可用，且該分支目前未啟用，不值得為它承擔改寫風險。

5. **以依賴注入取代 monkey patch（遵循 `config.yaml` 的 tasks 規則）**
   將實際合成動作抽成可注入的介面（例如 `TTSService(engine=..., fallback_engine=...)`），於唯一組裝點 `app/dependencies.py:281` 注入正式實作。新測試傳入 fake engine 驗證「語言對應、rate 字串、fallback 觸發」，不觸網、不 monkey patch。
   既有 `test_tts_service.py:41`（monkeypatch `requests.post` 的 n8n 測試）屬既有資產，本 change 不重寫。

6. **voice 對照表集中管理**
   `VOICE_BY_LANGUAGE`：`zh-TW / en / ja / th / vi / id` 各對應一個 neural voice，未知語言 fallback `zh-TW`。
   *實作第一步*：跑 `edge-tts --list-voices` 核對名稱（設計階段所列名稱未經實測驗證），泰／越／印尼三者尤須確認。

7. **設定頁同時補上語音開關**
   `voice_reply_enabled` 已在 `ApiUserSettings` 型別中但 UI 未渲染。既然要新增語音區塊，一併把開關做出來（加入 `toggleFieldMap` 即可），避免只有語速可調卻無處開關的怪狀。Rich Menu 的一鍵切換維持不動，兩者為同一份設定的兩個入口。

## Risks / Trade-offs

- **edge-tts 無 SLA、端點可能改版** → 由 Decision 3 的 gTTS fallback 承接；且這不是新增風險（現行 gTTS 同樣非官方）。
- **k8s 連外未驗證** → CARE-infra 無 NetworkPolicy／egress 限制，政策上不擋，但實際連通性須部署後確認；fallback 可在此期間兜底。
- **發音品質無法自動驗收** → 「產出合法 mp3」不等於「泰文藥名唸得對」。藥名／劑量／院所名需人耳驗收；兩種引擎皆不支援發音辭典，此為已知天花板。
- **混語句子**（中文回覆夾英文藥名）只會用單一 voice 唸，任何單 voice 方案下皆無解。
- **既有不一致，本 change 不處理**（避免擴大 blast radius，但列此備查）：
  1. `voice_reply_enabled` 預設值不一致 —— `UserSettings` 為 `False`（`user.py:48`，`dispatcher.py:285` 註解亦然），但 `message_handler.py:199` 的 fallback 為 `True`；舊資料缺欄位者會被視為已開啟。
  2. 兩條寫入路徑範圍不同 —— `update_voice_reply_enabled`（`user_profile_repository.py:79-92`）同時寫頂層與 `settings.*`，`update_user_settings`（`:64`）只寫 `settings.*`。目前讀取端皆優先讀 `settings.*` 故未出事。
- **時長估算**：`_get_duration_ms` 的字數 ×250ms 估算僅在 mutagen 讀取失敗時觸發，對拼音語言會低估；edge-tts 同為 mp3，實務上走 mutagen 路徑，不改。

## Migration Plan

無資料 migration。`settings.voice_rate` 缺值時由 `UserSettings` default 補為 `normal`（`get_user_settings` 本就把 raw dict 灌入 pydantic model）。前端 `defaultSettings` 同步加預設值，未登入時 fallback localStorage 的既有行為不變。

## Open Questions

- 三檔語速常數（-25 / 0 / +25）需在 edge-tts 實聽後校準，是否要把 `normal` 也往上調（使用者反映現行語速偏慢）待驗收決定。
- gTTS fallback 的保留期限：建議觀察一個發布週期後回頭評估。
