## Why

CARE 目前對「使用者正在接觸一個有問題的藥」這件事**完全沒有反應能力**。

長輩在聊天室問「這個日本帶回來的藥可以吃嗎」，或直接拍一張藥盒照片，訊息會走 `dispatcher.py` → handler → agent，由 RAG 與 Gemini 生成一段回覆，然後結束。這條路徑有兩個缺口：

1. **沒有權威的核准與否判定。** agent 的回答來自模型知識與 RAG 檢索，不是來自食藥署藥證資料。`resources/drug_catalog.json`（66,478 筆核准藥證）在專案裡已經存在，但目前只被 `DrugCatalogService` 用於藥袋辨識的錯讀偵測，對話路徑讀不到它。
2. **沒有人會知道。** 就算 agent 當下回了「這個藥台灣沒有核准」，這件事只發生在長輩與 bot 的一對一聊天室裡。家屬完全不知情，也就無從介入。

第 2 點才是真正的問題。長輩對來路不明藥物的信任通常來自人際關係（朋友介紹、子女從國外帶回、電視購物主持人），**單靠一則 bot 訊息闢謠，在說服力上敵不過那個關係**。要改變行為，需要家人在場一起談。CARE 已經有族譜（`app/models/family_tree.py`）與推播管道（`reply.py:146` 的 `push_flex`），缺的只是把兩者接到風險偵測上。

同時，最典型的案例證明「查藥證庫查不到就是未核准」這個直覺是錯的：

```
使用者說的：合利他命強效錠 EX PLUS（日本原裝進口）
藥證庫實查：衛署藥輸字第025431號  合利他命 強效錠     ← 台灣有核准
            衛部藥輸字第027584號  合利他命® 金強效錠   ← 台灣有核准
```

`DrugCatalogService` 的含容比對會把空白正規化掉，「合利他命強效錠」因而成為「合利他命強效錠EX PLUS」的子字串 → 命中 → 判定為已核准。**最該被攔下的案例，用藥證庫單獨判定會是 false negative。**

真正的風險不是「衛福部沒有這個藥」，而是「**你手上那個境外版本，跟台灣核准的那個不是同一個東西**」——配方、劑量、標示語言、通路都不同，且未經我國查驗登記。判定必須同時看「藥名在不在藥證庫」與「取得管道是不是境外／不明」，缺一不可。

### 為什麼不需要新的影像路徑

既有的圖片管線（`media_handler` → `mutimedia_processor` → `MEDIA_PARSE_WEBHOOK_URL` → n8n）已經提供了本能力需要的一切。n8n 的圖片節點是一個嚴格的 OCR 引擎，其提示詞明訂：

```
You are an OCR extraction engine.
Extract all visible text from the provided image.
- Preserve original formatting (line breaks, spacing, punctuation) as much as possible.
- Do not hallucinate text that is not visible in the image.
```

也就是說，回到 CARE 的是**圖片上的完整文字**，不是一段概括描述。一張日本藥盒的 OCR 結果會包含品名、型號後綴（`EX PLUS`）與日文標示——這些正是判定所需的訊號，全部都在。

因此圖片不需要任何新的處理：`BaseLineMessageHandler._process_and_reply()` 是文字與媒體訊息的共用匯流點，圖片走到那裡時 `user_text` 已經是 OCR 全文。**在該處掛一個 hook，同時覆蓋文字與圖片兩種輸入，且不新增任何影像下載或模型呼叫。**

## What Changes

- **新增風險偵測能力**：新增 `app/services/safety/` 模組。使用者的文字訊息與圖片 OCR 文字，於主回覆流程之外併行做一次風險評估，得到 `none`／`low`／`high` 三級結果。
- **單一接入點**：hook 掛在 `BaseLineMessageHandler._process_and_reply()`。文字與圖片共用，SHALL NOT 新增任何影像處理路徑。
- **外文字符集偵測為純函式**：日文假名、韓文、泰文等非中文字符集以 Unicode 區間直接偵測，SHALL NOT 依賴模型自述語言。中文藥證品名不可能含假名，日文藥品包裝幾乎必然含假名——這是字元層級的判斷，比關鍵字黑名單可靠且沒有「追不完」的問題。
- **模型只抽取，不判斷**：`DrugMentionExtractor` 以 schema 約束的 structured output 抽出「藥名、來源描述、取得通路、調劑包裝訊號」等**事實**，SHALL NOT 輸出風險結論。風險等級由 `risk_rules.assess()` 這個純函式決定。
- **藥證庫直接重用**：呼叫既有的 `DrugCatalogService.match()`，不新增比對邏輯、不新增資料源。
- **分級揭露**：`low` 只以 push 回覆當事人本人；`high` 才推播族譜成員，**且同時告知當事人已通報**。SHALL NOT 靜默通報。
- **辨識合法調劑包裝**：抽取結果帶有病患姓名、調劑機構、調劑者、調劑日期等法定必載欄位時，視為合法醫療機構調劑，`low` SHALL NOT 送出訊息——否則使用者拍一張正常藥袋，只要其中一個藥名沒命中藥證庫就會被要求「再拍一次包裝」。
- **通報收件人為族譜全員**：目前沒有權限管理，且可疑提問不像用藥提醒有 `creator_user_id` 這種天然錨點（`medication_scheduler.py:417`）。收斂收件人待權限管理完成後另案處理。
- **通報節流**：新增 `safety_alerts` collection，以 `(user_id, drug_key)` 唯一索引原子取得通報權，TTL 自動過期。
- **不阻塞主回覆**：風險評估與主回覆併行，全程以 push 送出，不佔用 reply token。評估失敗時靜默略過，SHALL NOT 讓正常對話失敗。

## Capabilities

### New Capabilities

- `drug-safety-alert`：可疑用藥提問的風險偵測、外文字符集與取得通路的訊號辨識、藥證庫複合判定、三級分流、合法調劑包裝的誤報抑制、族譜通報與知情揭露、通報節流、失敗時的降級行為

### Modified Capabilities

無。本 change 只在既有的共用匯流點掛一個併行 hook，不改變 `line-reply-rules`、`agent-architecture`、`rag-responses`、`medication-identification` 任何一條既有需求文字。

（`backend-architecture` 不列入：新增 `app/services/safety/` 子模組與一個 repository 只是依循既有的「分層與放置」「依賴注入與組裝點」「測試目錄對齊」條文。）

## Impact

- **CARE**：`app/models/safety.py`（新增）、`app/repositories/safety_alert_repository.py`（新增）、`app/services/safety/risk_rules.py`（新增）、`app/services/safety/drug_mention_extractor.py`（新增）、`app/services/safety/safety_alert_service.py`（新增）、`app/services/line_messaging/flex/safety_flex.py`（新增）、`app/services/line_messaging/handler/message_handler.py`、`app/services/line_messaging/handler/media_handler.py`（僅建構子參數傳遞，無邏輯變更）、`app/db/mongodb.py`、`app/core/config.py`、`app/dependencies.py`、`app/i18n/messages.py`、`.env.example`
- **API**：無新增或變更的 HTTP 端點。本能力完全在 LINE webhook 路徑內運作。
- **測試**：`tests/unit/models/test_safety_models.py`（新增）、`tests/unit/repositories/test_safety_alert_repository.py`（新增）、`tests/unit/services/safety/test_risk_rules.py`（新增）、`tests/unit/services/safety/test_drug_mention_extractor.py`（新增）、`tests/unit/services/safety/test_safety_alert_service.py`（新增）、`tests/unit/services/line_messaging/test_safety_flex.py`（新增）、`tests/unit/services/line_messaging/test_message_handler.py`、`tests/unit/services/line_messaging/test_media_handler.py`
- **設定**：新增 `SAFETY_ALERT_ENABLED`（預設 `false`，功能開關）、`SAFETY_ALERT_DEDUPE_HOURS`（預設 24）、`SAFETY_ALERT_TIMEOUT_SECONDS`（預設 20）
- **相依**：沿用既有 `GeminiService`（`app/dependencies.py:71`）與 `GEMINI_API_KEY`，沿用既有 `DrugCatalogService` 與 `resources/drug_catalog.json`。**不新增外部服務、不新增金鑰、不新增資料源、不新增套件。**
- **不受影響**：`CARE-n8n`（本 change 不修改該 repo 任何檔案，不改 n8n workflow）、`app/services/media/mutimedia_processor.py`（一行不改）、`CARE-LIFF`（無前端變更）、`medication_scheduler`、`rich-menu`、藥袋掃描的三支端點、圖片／影片／語音／檔案的既有處理與回覆行為
- **行為變更**：`SAFETY_ALERT_ENABLED=false` 時零行為變更。開啟後，符合條件的訊息會額外產生 push 訊息給當事人與族譜成員；主回覆的內容與時序不變
- **成本**：文字路徑有純函式前置篩選（`looks_drug_related()`），未命中不呼叫模型。圖片不新增任何下載或模型呼叫——OCR 已由既有管線完成，本能力只消費其產出的文字
- **隱私**：通報訊息 SHALL 只含當事人姓名、藥名與風險類型，SHALL NOT 含使用者的原始提問文字或 OCR 全文——原話與藥袋 OCR 都可能包含病情與病患姓名，與 `medication-identification` 的「適應症不進推播」是同一條原則
- **刻意不做**：修正 `mutimedia_processor` 的事件迴圈阻塞（`process_media()` 宣告為 `async` 但內部以同步 `requests` 呼叫，最長阻塞 120 秒；這是既有缺陷，與本能力的動機無關，應獨立提案）；置換或修改任何既有的媒體處理路徑；轉傳謠言長圖的辨識與事實查核；通報歷史查詢頁；家人端已讀／已處理狀態機；藥物交互作用與劑量安全性檢查；收件人的權限分級
