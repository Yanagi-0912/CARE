## Why

後端 `POST /api/knowledge-reports` 早就存在（`app/routers/users/knowledge_reports.py:26`）且有測試，但前端從未接。目前知識回報只有兩條自動來源：agent tool `submit_knowledge_report`、web fallback 自動建報（`app/services/rag/web_search_service.py:72` → `create_from_web_fallback`）。使用者若知道某頁衛教資料已過時、或知識庫根本沒收錄某筆，只能繞回 LINE 聊天讓 LLM 代為判斷要不要開報告。

LIFF 知識回報頁（`CARE-LIFF/src/pages/KnowledgeReports/index.tsx`）目前是純唯讀列表，沒有任何表單。本 change 補上「手動回報」入口：只要 URL 與說明兩欄，admin 看說明欄＋URL 就能判斷該不該收。這是次要功能，因此設計取向是「把既有端點接起來並補上必要的護欄」，不是重做回報流程。

護欄之所以必要：表單一開放，`user_source_urls` 就成為使用者可任意控制、且核准後會被 scrape 進向量庫的輸入。因此建立當下就要驗白名單（依賴 change 1 `harden-url-whitelist` 修好的 `is_allowed_url`／`normalize_url`／`assert_allowed_urls`），並補上目前完全不存在的濫用防護（全 app 只註冊了 CORS middleware）。

## What Changes

- `CreateKnowledgeReportRequest`（`app/models/knowledge_report.py:47`）：`user_source_urls` 由選填改必填（1–3 個、單一 URL ≤ 2048 字元）、`user_note` 由選填改必填（trim 後 1–500 字元）、`question` 補 `max_length=500`；空白字串以 field validator 拒絕（`min_length` 不會擋掉 `"   "`）。
- **建立當下驗白名單，且驗證只放在 router 層**：`create_knowledge_report` 端點呼叫 change 1 的 `assert_allowed_urls`，一次回報全部不合格的 URL。`KnowledgeReportService.create()`（`service.py:40`）本身 **不** 加驗證——`create_from_web_fallback`（`service.py:63`）內部就是呼叫 `create`，把驗證塞進去等於讓白名單一收緊就讓自動建報失敗，而該失敗會被 `web_search_service.py:98` 的 `except Exception` 吞掉、無人察覺。
- **agent tool 維持 URL 選填**：`submit_knowledge_report`（`app/tools/knowledge_report_tools.py:24`）的 `user_source_urls` 仍是 `list[str] | None = None`。強制必填等於要求 LLM 在使用者沒給連結時自行生一個 URL 才能完成工具呼叫，而幻覺出的 `gov.tw` 連結會通過白名單、進佇列、可能被核准去 scrape。tool 收到不合白名單的 URL 時 **過濾並記錄**，SHALL NOT 因此讓工具呼叫失敗。
- 新增 `KnowledgeReport.source`（`manual`／`agent_tool`／`web_fallback`，舊紀錄為 `None`）。存在理由有二：配額若把自動建報也算進去，使用者多問幾題就會把自己的手動額度吃光；admin 也需要知道某筆的 URL 是使用者親手貼的還是 LLM 代填的。
- **手動回報配額**：以 `line_user_id` 為單位，24 小時內最多 `KNOWLEDGE_REPORT_MANUAL_DAILY_QUOTA`（預設 10）筆 `source="manual"` 的回報，超過回 429。只計手動，自動路徑不受限也不佔額度。
- **`report_id` 碰撞重試**：`_generate_report_id`（`service.py:35`）只有 4 碼隨機，`repository.insert`（`knowledge_report_repository.py:32`）對 `knowledge_report_id` unique index 的 `DuplicateKeyError` 沒有處理。表單開放後這會變成 500，而前端 `parseError` 會把它渲染成一段錯誤字串，讓人誤讀成「白名單擋掉了」。改為重新產生編號最多重試 5 次。
- 前端（`CARE-LIFF`）：`knowledgeReportsApi.ts` 新增 `createKnowledgeReport`；知識回報頁新增回報表單 Dialog 與 `/knowledge-reports/new` 深連結路由；送出成功後 `invalidateQueries({ queryKey: queryKeys.knowledgeReports })`。
- 前端顯示補齊：`KnowledgeReports/index.tsx` 的 `interface KnowledgeReport`（:22）、`mapReportDto`（:55）、詳情 Dialog（:333 的 `<dl>`）三處都補上 `user_source_urls` 與 `user_note`，否則使用者送出後在自己的列表裡看不到自己填了什麼。
- i18n：`CARE-LIFF/src/i18n/messages.ts` 的 `knowledgeFeatureMessages`（:144）新增表單與錯誤文案，六語言（zh-TW／en／id／vi／th／ja）同步。

### 明確不做（刻意的範圍排除）

- **不接 `delete_pending_or_reviewing_by_urls`**（`knowledge_report_repository.py:217`）。該函式全專案只有一個呼叫點：`service.py:74`，在 `create_from_web_fallback` 內。它是 `delete_many`、filter 不含 `line_user_id`、硬刪且無 tombstone。手動表單一旦接上，使用者 A 只要貼一個 URL 就能讓使用者 B 的待審回報永久消失——那是一個「刪除他人資料」的原語，不是去重。手動回報因此 **不做任何刪除式去重**；同 URL 重複出現在佇列由 admin 目視處理。這條寫進 spec 是為了避免日後有人看到 `create` 沒去重就「順手接上」。
- 不為 `reason` 引入任何分支邏輯。`reason` 目前是零分支純標籤（全後端沒有一處讀 `report.reason`），本 change 只是讓它出現在表單，維持純標籤。
- 不做編輯／撤回已送出的回報。
- 不在前端重新實作白名單判斷（理由見 design.md 決策 7）。

## Capabilities

### New Capabilities

- `user-knowledge-reports-ui`：LIFF 使用者端知識回報頁的行為契約——手動回報表單、送出後的列表更新、白名單／配額錯誤的呈現，以及「使用者填的內容自己看得到」。既有的 `admin-knowledge-reports-ui` 只涵蓋審核端，使用者端一直沒有對應 spec。

### Modified Capabilities

- `knowledge-reports`：建立回報的約束（URL 必填並於建立時驗白名單、`user_note` 必填、數量與長度上限、每使用者配額、`source` 標記、`report_id` 碰撞重試）；同時把「同 URL 待審回報去重」限縮為只適用 web fallback 自動建報，並明文寫死 agent tool 的 URL 維持選填。
- `admin-knowledge-reports-ui`：修改現行第 21 行「使用者回報時 SHALL NOT 被要求附上來源 URL」——手動表單之後這句不再成立。但 admin 自行補 URL 的機制 **必須保留**：舊資料與 agent tool 路徑仍會產生無 URL 的回報。

## Impact

- **程式（後端）**：`app/models/knowledge_report.py`、`app/routers/users/knowledge_reports.py`、`app/services/knowledge_reports/service.py`、`app/repositories/knowledge_report_repository.py`、`app/tools/knowledge_report_tools.py`、`app/core/config.py`、`.env.example`
- **程式（前端）**：`CARE-LIFF/src/api/knowledgeReportsApi.ts`、`CARE-LIFF/src/pages/KnowledgeReports/index.tsx`、新增 `CARE-LIFF/src/pages/KnowledgeReports/ReportFormDialog.tsx`、`CARE-LIFF/src/pages/KnowledgeReports/styles.ts`、`CARE-LIFF/src/App.tsx`、`CARE-LIFF/src/i18n/messages.ts`
- **API 契約**：`POST /api/knowledge-reports` 的請求主體收緊（新增 400 `url_not_allowed`／`url_invalid`、429 `quota_exceeded`；缺欄位為 422）。這是 breaking change，但既有呼叫端只有測試——前端從未呼叫過此端點。
- **依賴**：依賴 change 1 `harden-url-whitelist` 提供的 `normalize_url` / `assert_allowed_urls` 與強化後的 `is_allowed_url`；依賴 change 2 `approve-with-content-preview` 讓 admin 在核准前看得到實際抓到的內容（本 change 讓使用者可控 URL 進入佇列，若沒有 change 2，admin 仍只能看網址字串就決定要不要收）。
- **設定**：新增 `KNOWLEDGE_REPORT_MANUAL_DAILY_QUOTA`（預設 10）、`KNOWLEDGE_REPORT_MAX_SOURCE_URLS`（預設 3）
- **測試**：`tests/unit/routers/test_knowledge_reports.py`、`tests/unit/services/knowledge_reports/test_service.py`、`tests/unit/tools/test_knowledge_report_tools.py`、`tests/unit/repositories/test_knowledge_report_repository.py`、`CARE-LIFF/src/tests/knowledgeReports.test.tsx`、`CARE-LIFF/src/tests/i18n.test.ts`
- **資料**：`source` 為新增的可選欄位，既有文件沒有此欄位即為 `None`；無需 migration。不新增索引——配額查詢用既有的 `knowledge_report_line_user_created`（`knowledge_report_repository.py:21`，涵蓋 `line_user_id` + `created_at`）。
