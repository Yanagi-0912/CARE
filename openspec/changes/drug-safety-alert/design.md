## Context

本能力要在既有的 LINE 對話路徑上，加一條「偵測到風險就把家人叫來」的旁路。它踩在四塊已經存在、且各自有明確約束的東西上：

1. **藥證庫**（`app/services/medication/drug_catalog_service.py`）。66,478 筆核准藥證，三階段比對（完全 → 含容 → 模糊），已有 n-gram 反向索引與單元測試。本能力對它的需求只有一句：「這個名字在台灣有沒有核准」。
2. **族譜與推播**（`app/models/family_tree.py`、`reply.py:146` 的 `push_flex`）。收件人集合與送出管道都是現成的。
3. **既有圖片管線**（`media_handler` → `mutimedia_processor` → n8n）。n8n 的圖片節點是嚴格的 OCR 引擎，回到 CARE 的是圖片上的完整文字。
4. **共用匯流點**（`BaseLineMessageHandler._process_and_reply()`）。`LineMessageHandler` 與 `LineMediaHandler` 都繼承自它，文字與圖片在此處都已經是純文字。

本設計的核心約束有三個：

1. **判定邏輯必須是可測試的純函式。** 通報家人是高代價且不可逆的動作——訊息推出去就收不回來，而收件人是一整個家庭。這類決定不能取決於一次模型呼叫的輸出穩定性。這與 `_resolve_slots` 把「PRN 不排時段」寫死在程式碼裡是同一條原則。
2. **可測試，且不得使用 monkey patch。** 依 `openspec/config.yaml` 的 `rules.tasks`，測試替身一律以依賴注入傳入。
3. **不得讓正常對話變慢或變壞。** 這是一條加值旁路。它掛掉的時候，使用者應該完全感覺不到。

### 與 `medication-identification` 的界線

`medication-identification` 的 Non-Goals 明列「LINE 聊天室直接傳照片的自動偵測分流」。**本 change 不推翻那條。** 兩者處理的是不同的問題：

| | `medication-identification` | 本能力 |
|---|---|---|
| 用途 | 把藥袋內容**建檔**成藥品與提醒 | 判斷提問有沒有風險 |
| 入口 | LIFF 主動上傳（使用者已表達意圖） | 聊天室被動偵測 |
| 錯誤代價 | 建錯提醒 → 錯誤服藥 | 誤報 → 打擾家人 |
| 輸出 | 寫入 `medications` | 只送訊息，不寫任何用藥資料 |

藥袋建檔仍然只走 LIFF。本能力 SHALL NOT 依偵測結果建立任何藥品或提醒，也 SHALL NOT 對圖片做任何新的處理——它只讀既有管線已經產出的文字。

## Goals / Non-Goals

**Goals：**

- 讓「使用者正在接觸未經我國核准、或雖核准但取自境外／不明通路的藥品」成為系統能偵測到的事件
- 偵測到高風險時，把家人拉進這場對話，而不是只留下一則長輩會忽略的 bot 訊息
- 判定邏輯全部落在純函式裡，可用表格驅動測試窮舉，不需要呼叫模型即可驗證
- 全部新增邏輯落在 `app/services/safety/` 之下，與既有 handler 的耦合面小到可以整條移除

**Non-Goals：**

- **修正 `mutimedia_processor` 的事件迴圈阻塞。** `process_media()` 宣告為 `async`，但 `_download_media_to_tmp()` 與 `_extract_user_text_via_webhook()` 都是同步 `def` 使用 `requests` 且未經 executor 包裝，因此鎖住事件迴圈最長達 `WEBHOOK_TIMEOUT_SECONDS`（120 秒），而用藥提醒排程器跑在同一個行程。這是一個真實且嚴重的既有缺陷，本專案也已為同一類失效模式（400~750ms 的同步比對）做過一次根本性重構——但它與本能力的動機無關，且修正它需要改動他人維護的檔案並先做 DI 重構。**明確記錄於此，應獨立提案。**
- 修正 `mutimedia_processor` 把例外收斂成字串當正常回傳值的行為（同上，可獨立提案）
- 置換或修改任何既有的媒體處理路徑
- 圖上沒有文字時的使用者體驗（目前會要求重拍）——屬於既有圖片管線的範圍
- 轉傳謠言長圖的辨識與假訊息事實查核
- 藥物交互作用、重複用藥、劑量安全性等臨床判斷
- 通報歷史查詢介面、家人端已讀／已處理狀態
- 收件人的權限分級與細緻授權
- 阻止使用者服用任何藥物——本能力只提供資訊與把家人叫到場，不做任何形式的封鎖

## Decisions

### 決策 1：重用既有圖片管線，不新增任何影像路徑

**選擇**：hook 掛在 `BaseLineMessageHandler._process_and_reply()`。文字訊息與圖片訊息在此處都已經是純文字，一個接入點同時覆蓋兩者。本能力 SHALL NOT 下載影像、SHALL NOT 對影像呼叫模型。

**促成這個選擇的事實**——n8n 的圖片節點不是「看圖說話」，而是嚴格的 OCR 引擎：

```
You are an OCR extraction engine.
Extract all visible text from the provided image.
- Preserve original formatting (line breaks, spacing, punctuation) as much as possible.
- Detect all languages present using ISO 639-1 codes (e.g., "en", "zh", "ja").
- Do not hallucinate text that is not visible in the image.
```

回到 CARE 的是圖片上的完整文字。一張日本藥盒的 OCR 結果會包含品名、型號後綴（`EX PLUS`）與日文標示——判定所需的訊號全部都在，不需要為了拿到它們而另外處理一次影像。

**替代方案 A — 自建原生影像管線，置換 n8n 圖片分支**：可以順帶解掉事件迴圈阻塞，並拿到 schema 約束的結構化欄位。否決有三個理由：一是它把「純新增的旁路」變成「取代既有子系統」，風險等級完全不同，且需要一整套行為對等驗證才敢開啟；二是既有 OCR 已經提供了判定所需的全部訊息，重做一次拿不到額外價值；三是事件迴圈阻塞是獨立於本能力的既有缺陷，把它綁進來會讓兩件事一起卡住——已列入 Non-Goals 並建議獨立提案。

**替代方案 B — 並行旁路，既有路徑照跑、另開一條自己抽取**：既有行為零變更，但同一張圖要付兩次下載與兩次模型呼叫。否決：成本翻倍，換到的仍然只是既有 OCR 已經給出的資訊。

**已驗證的顧慮，結論是不成立**：曾擔心「依賴 n8n 會讓通報在 n8n 停止服務時靜默失效」。實際追過程式碼後不成立——n8n 失敗時 `process_media()` 回傳「發生錯誤，請忽略此內容或重新嘗試。」，`media_handler.py:97` 的字串比對命中後拋出 `LineValidationError`，使用者會收到明確的錯誤訊息。**圖片回覆與風險偵測共用同一個輸入，輸入沒了兩邊一起失效，而使用者從圖片回覆那一側就會發現。** 不存在無聲失效的路徑。

**代價**：圖片偵測的品質綁在他人維護的 OCR 提示詞與模型選擇上。這是真實的耦合，見 Risks。

### 決策 2：外文字符集偵測是純函式，不依賴模型自述語言

**選擇**：以 Unicode 區間直接偵測非中文字符集：

```python
JAPANESE_KANA = re.compile(r'[぀-ゟ゠-ヿ]')  # 平假名 + 片假名
KOREAN        = re.compile(r'[가-힯]')
THAI          = re.compile(r'[฀-๿]')
```

**理由**：中文藥證品名不可能含假名；日文藥品包裝幾乎必然含假名（助詞與外來語片假名）。這是字元層級的事實判斷，不是關鍵字比對——沒有黑名單「永遠追不完」的問題，也不需要模型自述語言（模型可能判錯，字元不會）。

n8n 的 OCR 節點其實也算出了 `languages` 與 `primary_language`（ISO 639-1），但其 `Respond to Webhook` 節點只回傳 `{{ $json.text }}`，這兩個欄位在 n8n 端就被丟棄，從未送達 CARE。要取用就得修改他人維護的 workflow——而字元集偵測不需要，且更可靠。

**拉丁字母不列入訊號**：台灣核准藥證的英文品名本來就是拉丁字母（`LIPITOR`、`PANADOL`），列入會全面誤報。歐美代購因此偵測不到，是已知的覆蓋缺口，見 Open Questions。

**訊號分工**：字符集訊號來自原文（可靠、純函式）；取得通路訊號來自模型抽取的 `channel`（使用者說「朋友介紹的」不會出現在包裝上）。兩者互相獨立，任一命中即可。

### 決策 3：模型只抽取事實，風險等級由純函式決定

**選擇**：

```
user_text（使用者打的字，或圖片的 OCR 全文）
   ├─ looks_drug_related(text, catalog)      純函式，擋掉無關訊息，未通過則不呼叫模型
   ├─ detect_foreign_scripts(text)           純函式，字元集訊號
   └─ DrugMentionExtractor.extract(text)     Gemini，schema 約束
        {raw_name, source_text, channel, dispensed_package_markers}   ← 事實，不含結論
           ↓ DrugCatalogService.match(raw_name)                        ← 既有服務
        {catalog_hit, license_number}
           ↓ risk_rules.assess(mention, foreign_scripts)               ← 純函式，無 I/O
        RiskLevel: none | low | high
```

抽取器的 schema **不包含任何風險欄位**。提示詞明確要求「只描述使用者說了什麼，不要判斷安不安全」。

**替代方案 — 一次呼叫直接吐 `risk_level`**：實作最短，最能處理沒見過的表達方式。否決：把「要不要驚動全家」的決定權交給一個輸出會漂移的模型，而誤報一次的代價（長輩感覺被監視、從此不再發問，改去問 LINE 群組裡的朋友——正好是本能力想擋的資訊來源）遠大於漏報一次。且風險門檻若埋在提示詞裡，就無法為它撰寫窮舉測試。

**替代方案 — 純規則，不呼叫模型**：零成本、完全可預測。否決：長輩用自己的話描述（「我女兒從日本帶回來的那個，說是對神經痛很好」），關鍵字比對接不住藥名與通路。不過它的規則部分仍然存在——就是 `assess()` 與 `detect_foreign_scripts()`。

**附帶好處**：`assess()` 是純函式，`合利他命強效錠 EX PLUS` 這種指標案例可以寫成 table-driven test 永久釘住，不需要模型參與。

### 決策 4：判定規則是「藥證庫 × 取得訊號」的複合矩陣

**這是本能力存在的技術理由。** 以藥證庫實查驗證過：

```
衛署藥輸字第025431號  合利他命 強效錠
衛部藥輸字第027584號  合利他命® 金強效錠
```

含容比對把空白正規化後，「合利他命強效錠」是「合利他命強效錠EX PLUS」的子字串 → 命中 → 若單看藥證庫，判定為已核准、不通報。而這正是最該攔下的案例。

| 藥證庫 | 取得訊號 | 風險 | 理由 |
|---|---|---|---|
| 任意 | `tv_shopping`／`acquaintance`／`online_marketplace` | `high` | 不明通路本身即風險，與藥名核准與否無關 |
| 未命中 | 有外文字符集訊號或 `overseas_personal` | `high` | 未核准 + 境外取得，最典型的風險組合 |
| 未命中 | 無其他訊號 | `low` | **可能只是俗稱、簡稱或錯字**，證據不足以驚動全家 |
| 命中 | 有外文字符集訊號或 `overseas_personal` | `high` | 同名不同版本 ← 合利他命 EX PLUS 落在這格 |
| 命中 | 無其他訊號 | `none` | 台灣核准藥、正常通路，不介入 |

第三列是刻意的保守設計。藥證庫未命中的最常見原因不是「這個藥沒核准」，而是使用者講的是俗稱（「消炎藥」）、簡稱或打錯字。把這一格判成 `high` 會產生大量誤報，而誤報正是這個功能最容易失敗的方式。

### 決策 5：辨識合法調劑包裝，避免對藥袋照片誤報

**問題**：走過使用情境後發現的缺陷。使用者在聊天室拍一張正常藥單，OCR 出來通常有 3～5 個藥名。只要**其中任何一個**沒命中藥證庫（罕見藥、新核准品項、OCR 讀錯一字），就落到決策 4 的第三列 → `low` → 推播給本人「這個名字我查不到，可以拍一下包裝給我看嗎？」。使用者剛剛才拍了包裝。藥單是聊天室最常被拍的東西之一，代表這個誤報會**經常發生**。

**選擇**：抽取 schema 增加 `dispensed_package_markers`，收集藥袋法定必載欄位的訊號（病患姓名、調劑機構名稱、調劑者、調劑日期）。衛署藥字第0910033863號要求藥品調劑包裝必須標示這些欄位，因此它們同時出現是「這是合法醫療機構調劑」的強訊號。命中時 `channel` 判定為 `medical_institution`，且 `low` SHALL NOT 送出任何訊息。

**為什麼不影響 `high`**：`high` 的觸發條件是不明通路或外文字符集訊號。一張台灣的合法調劑藥袋不會帶有這些訊號；若真的同時出現（例如藥袋照片旁邊擺了一盒日本代購藥），那確實值得通報。因此這條規則只壓 `low`，不壓 `high`。

**附帶好處**：藥單 OCR 全文含病患姓名與醫院名稱。壓掉這個誤報同時也關掉了一條資訊洩漏路徑。

### 決策 6：分級揭露，SHALL NOT 靜默通報

風險等級為 `high` 時，通報族譜成員的**同一次流程也告訴當事人**：「這個藥台灣沒有核准／你拿到的不是台灣核准的版本，我已經請家人一起看看。」

**替代方案 — 靜默通報**：保住長輩繼續發問的意願，家人可以自己找時機開口。否決：被發現就是不可逆的信任崩塌，且與專案既有的隱私調性（適應症不進推播、功能關閉時回 404 而非「功能未開放」）方向相反。

`low` 只回當事人、不通報任何人，正是為了讓「透明」不等於「動不動就叫人」。

### 決策 7：收件人是族譜全員

**選擇**：`FamilyTreeRepository.get_by_user_id(user_id)` 取回的 `family_members` 全體。

**與既有家屬警報不同**：用藥提醒的逾時警報送給 `alert_notify_user_id`，而它取自 `reminder.creator_user_id`（`medication_scheduler.py:417`）——誰替家人建了那條規則，逾時就推給誰，單一收件人。本能力沒有這個錨點：沒有人「建立」過一次可疑提問，因此沒有天然的負責人。目前也沒有權限管理可用來區分誰該收到。族譜全員是唯一有明確定義的集合。

**代價**：族譜裡不負照顧責任的成員（孫輩、平輩）也會收到，對長輩而言接近公開處刑。這是已知且被接受的取捨。`FamilyMember.is_care_recipient` 已經存在，權限管理完成後是現成的收斂依據。

### 決策 8：通報節流以唯一索引原子取得，不做讀後寫

**選擇**：`safety_alerts` collection，`(user_id, drug_key)` 唯一索引 + `expires_at` TTL 索引（`expireAfterSeconds=0`，沿用 `prescription_draft_repository.py:28` 的寫法）。通報前 `insert_one`，`DuplicateKeyError` 即代表 TTL 內已通報過，直接跳過。

**理由**：「先查有沒有通報過、沒有才通報」在同一位使用者連送兩則相似訊息時，兩邊都會在查詢當下判斷未通報而各自推播一次。唯一索引讓「取得通報權」這件事本身就是原子的，不需要額外的 CAS。

`drug_key` 使用 `DrugCatalogService` 的正規化結果，讓「合利他命EX PLUS」與「合利他命 EX PLUS」視為同一個藥。

### 決策 9：與主回覆併行，全程 push，失敗即靜默

**選擇**：風險評估以 `asyncio.create_task` 與主回覆流程併行；所有輸出一律用 `push_flex`／`push_text`，不佔用 reply token。

**理由**：抽取要呼叫 Gemini，序列執行會讓每則訊息的回覆延遲增加數秒。使用者感受到的是「CARE 變慢了」，而他根本不知道背後多做了什麼。

**失敗行為**：抽取逾時、模型回應格式錯誤、藥證庫缺席、族譜查詢失敗——一律記 log 後靜默結束，不通報、不回覆、不影響主流程。**對主流程 fail-open，對通報 fail-closed。**

這與藥袋辨識刻意把失敗分成三種可區分原因的做法相反，是因為兩者的使用者處境不同：藥袋掃描是使用者主動發起、正在等結果，必須告訴他下一步做什麼；本能力是背景旁路，使用者沒有在等，一則「風險偵測失敗」的訊息只會造成困惑。

`push_flex` 送不出去同樣只記 log、不重試，沿用 `_notify_missed_summary` 的既有先例。

### 決策 10：分層與組裝

- `app/models/safety.py`：`DrugMention`、`AcquisitionChannel`、`RiskLevel`、`SafetyAlertRecord` 的 Pydantic 模型
- `app/repositories/safety_alert_repository.py`：節流記錄的存取，沿用 `collection: Optional[Any] = None` 慣例
- `app/services/safety/risk_rules.py`：`looks_drug_related()`、`detect_foreign_scripts()`、`assess()` 與 `normalize_drug_key()`，**全部為純函式：無 I/O、無類別、無模組層級狀態**。`looks_drug_related()` 需要的藥證庫索引以參數傳入
- `app/services/safety/drug_mention_extractor.py`：文字 → `list[DrugMention]`，建構子注入 `gemini_service` 與逾時
- `app/services/safety/safety_alert_service.py`：協調上述四者 + `DrugCatalogService` + `FamilyTreeRepository` + `LineReplier`
- `app/services/line_messaging/flex/safety_flex.py`：通報卡片
- 全部在 `app/dependencies.py` 組裝

handler 端只拿到一個 `safety_alert_service` 並呼叫一支方法。`LineMediaHandler` 只需要在建構子把該參數往 `super().__init__()` 傳，沒有任何邏輯變更。整條能力要移除時，handler 端的改動是兩行。

## Risks / Trade-offs

- **誤報疲勞讓功能自我廢除** → 這是本能力最可能的失敗方式。家人收到幾次無意義的通報後就會忽略通知，長輩發現「我一問就有人被叫來」之後就不再問，改去問朋友。三級分流、決策 4 第三列判 `low`、決策 5 的合法調劑辨識，三者都是為了壓低誤報。`SAFETY_ALERT_ENABLED` 預設關閉，讓判定規則能先以真實流量觀察誤報率再決定是否開啟。
- **偵測品質綁在他人維護的 OCR 上** → 圖片訊號來自 n8n 的 OCR 提示詞與模型選擇，兩者都不在本專案控制之下（實例：該 workflow 近期把模型由 `gemini-robotics-er-1.5-preview` 換成 `gemini-2.5-flash`，本能力不會收到任何通知）。緩解方式：單元測試一律以固定的 OCR 文字字串當輸入，不依賴實際跑 n8n；判定規則對輸入品質的退化是漸進的（讀不到字就抽不到藥名，落到不送訊息），不會產生錯誤方向的通報。
- **通報訊息本身洩漏病情** → 通報只帶姓名、藥名與風險類型，SHALL NOT 帶原始提問文字或 OCR 全文。推播會出現在通知列與鎖定畫面，可能被非預期的人看到——與 `medication-identification` 的「適應症不進推播」同一條理由。藥袋 OCR 全文同時含病患姓名與醫院名稱，更不得轉送。
- **`channel` 實際上多半抽不到** → 使用者很少明講取得管道，圖片上更不會有。此時判定退化為「藥證庫 + 字符集訊號」兩個維度。對日／韓／泰系代購仍然有效，對歐美代購失效，見 Open Questions。
- **前置篩選漏接** → `looks_drug_related()` 是黑名單性質，永遠追不完。漏接的方向是「該偵測的沒偵測」，不會產生誤報。可接受，但 SHALL 記錄命中率作為後續調整依據。
- **模型抽出不存在的藥名（幻覺）** → 提示詞要求只保留輸入文字中實際出現的字串，不得推測補齊；`raw_name` 為空的項目一律丟棄。即便如此仍抽錯時，藥證庫比對會未命中，落到 `low` 只回當事人，不會誤報給家人。
- **`asyncio.create_task` 的孤兒任務** → 任務必須被持有參考直到完成，否則可能被 GC 回收。實作時 SHALL 保留任務集合並在完成時移除，且任務內部的例外 SHALL 被捕捉並記錄，不得逸散成 "Task exception was never retrieved"。
- **長輩沒有族譜** → `get_by_user_id` 回 `None` 或成員為空時，`high` 仍然回覆當事人本人，只是沒有人可以通報。SHALL NOT 因此拋錯或跳過對當事人的回覆。
- **事件迴圈阻塞未被處理** → 已列入 Non-Goals。本能力不會讓它變嚴重（風險評估是併行的、不新增任何同步呼叫），但也不會讓它變好。它仍然是這個系統目前最嚴重的延遲來源，應獨立提案處理。

## Migration Plan

1. 資料模型與 repository（`safety_alerts` 與其兩個索引），此時無任何行為變更
2. `risk_rules.py`（純函式，可完整單元測試，含合利他命 EX PLUS 指標案例與藥單不誤報案例）
3. `drug_mention_extractor.py`（注入假 gemini invoker 測試）
4. `safety_alert_service.py` 與 flex 卡片
5. `app/dependencies.py` 組裝，`SAFETY_ALERT_ENABLED` 維持 `false`
6. handler 接入（`_process_and_reply` 一個 hook，涵蓋文字與圖片）
7. 開啟 `SAFETY_ALERT_ENABLED`，觀察誤報率與前置篩選命中率

**回滾**：把 `SAFETY_ALERT_ENABLED` 設回 `false` 即停用整條路徑。本能力不寫入任何用藥資料，`safety_alerts` 內容僅供節流且會自行 TTL 過期，無需資料回滾。

## Open Questions

- **歐美代購要用什麼訊號偵測？** 決策 2 的字符集判斷對日／韓／泰系有效，但歐美藥品包裝是拉丁字母，與台灣核准藥證的英文品名無法以字元層級區分。目前這類案例只能靠使用者主動提及通路（`channel`）才會被偵測到。可能的方向是劑量單位表述差異或 NDC 碼格式，但都沒有驗證過。在有真實樣本之前不猜。
- **「藥證庫未命中且無其他訊號」判 `low` 是否過於保守？** 這一格涵蓋了「真的是未核准藥，但使用者沒提通路、包裝也是中文」的情況，目前會被判成只回當事人、不通報。這個方向的錯誤是漏報。要決定它對不對，需要這一格的實際內容分布——有多少比例是俗稱／錯字（判 `low` 正確），有多少是真的未核准品項（判 `low` 是漏報）。這需要人工標註一批真實樣本，在有樣本之前不調整。
- **`SAFETY_ALERT_DEDUPE_HOURS` 訂 24 小時對嗎？** 這個數字是憑直覺選的。太短會讓長輩反覆問同一個藥時重複轟炸家人，太長會讓「隔天又在問」這個真正值得注意的訊號被吃掉。會回答這題的量測是重複提問的時間間隔分布。
- **圖片 OCR 文字帶著既有的前綴進入判定，是否需要剝除？** `media_handler` 會在 OCR 結果前加上「以下為使用者傳送的image媒體內容：」。該前綴不含藥名、不影響字符集偵測，目前決定原樣傳入不做剝除——剝除就得耦合到那個字串的格式，而它隨時可能被改。若日後發現它干擾抽取品質再處理。
