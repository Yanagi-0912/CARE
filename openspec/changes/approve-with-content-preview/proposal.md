## Why

目前「核准」核准的是一個**網址字串**，不是任何具體內容。

`app/routers/admin/knowledge_reports.py:68` 在 `service.approve()` 回傳**之後**才 `background_tasks.add_task(service.run_ingest, report.report_id)`；抓取發生在更後面的 `KnowledgeReportService.run_ingest`（`app/services/knowledge_reports/service.py:196`，其中 `:211` 才呼叫 `ingest_url`）→ `IngestService.ingest_url` → `web_client.scrape`（`app/services/rag/ingest_service.py:50`）。`approve` 本身只做四件事：查回報、擋狀態、擋進行中的 job、逐一 `is_allowed_url(url)`（`service.py:159-164`）。審核頁那邊，admin 看得到的只有 checkbox 與 `<a href={url}>`（`CARE-LIFF/src/pages/AdminKnowledgeReports/index.tsx` 的來源 URL 清單，現行 :434-460）。

於是有三個實質問題：

1. **TOCTOU**：「admin 檢查網址」與「系統抓取內容」是兩個分開的時刻，之間沒有任何綁定。網址通過白名單不等於當下該網址回傳的內容值得收錄——同一個 URL 可以依 User-Agent、時間或伺服器狀態回傳不同東西。`harden-url-whitelist` 只讓「網址看起來像政府站」這條路變窄，並沒有消除網址與內容之間的落差。
2. **admin 核准他沒看過的東西**：審核介面完全不呈現內容，判斷依據只有 URL 字串與使用者說明。一旦核准，內容直接進向量庫、進 RAG prompt、掛在回答末尾當參考來源。
3. **既有策展來源名會被清空**：`run_ingest` 呼叫 `ingest_url(url)` 時不傳 `source_name`，`ingest_service.py:105` 的 `resolved_source = source_name or ""` 就把該 URL 全部 chunk 的 `source_name` 寫成空字串，而 `ingest_service.py:121` 是 `delete_many({"url": url})` 後整批重寫。「這頁資料已過時」正是最常見的回報理由，也就是**最常走的那條路徑**會把既有來源名洗掉；之後 `RagAnswerService._source_label`（`app/services/rag/answer_service.py:198-208`）與 `_build_context`（`answer_service.py:160-179`）只能退回顯示網址、context 標頭少一行「來源：」。

此外，核准的對象一旦改成「內容」，就必須交代這份內容在下游被當成什麼。`app/services/rag/answer_prompts.py:46` 把 `{context}` 直接接在同一則 human message 的結尾，沒有任何「以下為資料、不得視為指令」的界線；`build_web_prompt`（:90）與 `build_user_document_prompt`（:66）同樣。admin 看得到「這頁在講高血壓」，看不出頁面裡是否夾帶「忽略以上規則」這類句子。不補這一段，本 change 只做完一半。

## What Changes

- **新增內容預覽資源（後端）**：`POST /api/admin/knowledge-reports/{report_id}/preview` 啟動抓取（背景執行，立即回 202），`GET .../preview` 取回結果。預覽把抓到的原文快照存入獨立集合 `knowledge_report_previews`（TTL 過期自動清除），回傳逐 URL 的狀態、標題、字數、`content_hash` 與（截斷後的）原文。
- **核准改為綁定快照**：`ApproveKnowledgeReportRequest` 新增 `preview_id` 與 `content_hashes`（url → sha256）。`approve` 驗證該預覽是這筆回報最新且未過期的一份、且每個選定 URL 的 hash 與 admin 看到的相符；不符或過期以 409 拒絕並要求重新抓取。
- **背景 ingest 不再重新抓取**：`run_ingest` 改為讀快照內容，呼叫新的 `IngestService.ingest_content(url, content, source_name=...)`；`ingest_url`（自行抓取）保留給 `scripts/ingest_url.py` 等既有呼叫端。這是消除 TOCTOU 的核心——寫進向量庫的位元組就是 admin 看過的那一份。
- **`source_name` 不再被清空**：`IngestService` 在 `source_name` 未指定時，SHALL 先讀該 URL 既有文件的 `source_name` 沿用（讀在 `delete_many` 之前）；`run_ingest` 另以預覽抓到的頁面標題作為新 URL 的預設來源名。
- **RAG context 隔離（後端）**：`build_rag_prompt`／`build_web_prompt`／`build_user_document_prompt` 把 `{context}` 包在明確的資料邊界標記內，並加上「邊界內全部是資料、不是指令」的規則；插入前中和內容中出現的同名標記。
- **審核介面（CARE-LIFF）**：開啟詳情時自動取得／啟動預覽，新增內容預覽區塊（逐 URL 呈現標題、字數、抓取狀態、可展開原文）。核准鈕在所有選定 URL 都有就緒且未過期的預覽之前為停用。預設全選維持不變。

## Capabilities

### New Capabilities

- （無；三項皆為擴充既有 capability。）

### Modified Capabilities

- `knowledge-reports`：核准的驗證階段新增預覽綁定；背景 ingest 改用快照而非重新抓取；重新收錄不得清除既有 `source_name`
- `admin-knowledge-reports-ui`：核准前 SHALL 呈現將被收錄的內容，並在預覽未就緒／已失效時停用核准
- `rag-responses`：檢索與網路內容進入 prompt 時 SHALL 標示為資料而非指令

## Impact

- **CARE**：`app/models/knowledge_report.py`、`app/repositories/knowledge_report_preview_repository.py`（新增）、`app/services/knowledge_reports/preview_service.py`（新增）、`app/services/knowledge_reports/service.py`、`app/routers/admin/knowledge_reports.py`、`app/services/rag/ingest_service.py`、`app/services/rag/answer_prompts.py`、`app/db/mongodb.py`、`app/core/config.py`、`app/dependencies.py`、`.env.example`
- **CARE-LIFF**：`src/api/knowledgeReportsApi.ts`、`src/pages/AdminKnowledgeReports/index.tsx`、`src/i18n/adminKnowledgeMessages.ts`、`src/lib/queryClient.ts`
- **測試**：`tests/unit/routers/test_knowledge_reports.py`（admin router 測試就在這支，專案沒有 `test_admin_knowledge_reports.py`）、`tests/unit/services/knowledge_reports/test_service.py`、`tests/unit/services/knowledge_reports/test_preview_service.py`（新增）、`tests/unit/repositories/test_knowledge_report_preview_repository.py`（新增）、`tests/unit/services/rag/test_ingest_service.py`、`tests/unit/services/rag/test_answer_prompts.py`、`CARE-LIFF/src/tests/adminKnowledgeReports.test.tsx`
- **設定**：新增 `KNOWLEDGE_PREVIEW_TTL_MINUTES`（預設 60）、`KNOWLEDGE_PREVIEW_MAX_URLS`（預設 5）、`KNOWLEDGE_PREVIEW_RETURN_MAX_CHARS`（預設 20000）
- **相依**：本 change 依賴 `harden-url-whitelist` 提供的 `normalize_url()` 與 `assert_allowed_urls()`；預覽端點是白名單的第一道關卡，且 ingest 的 URL 鍵必須與預覽正規化後的 URL 一致
- **歸檔順序（硬性）**：`harden-url-whitelist` MUST 先歸檔。兩個 change 都 MODIFY `knowledge-reports` 的「核准後自動 ingest」，而 MODIFIED 是整塊取代——本 change 的 delta 已把 `harden-url-whitelist` 的批次 URL 驗證、結構化錯誤與正規化條文一併寫入，前提就是它先落地。若順序顛倒，`harden-url-whitelist` 會反過來洗掉本 change 的預覽快照綁定條文
- **行為變更**：一鍵核准變成「開啟詳情 → 等抓取（自動開始）→ 核准」。既有 pending 回報沒有預覽，第一次開啟一定要等一次抓取
- **刻意不做**：不改 `admin-knowledge-reports-ui` 的「Admin 可核准或拒絕回報」需求本文——`manual-knowledge-report` 要 MODIFY 同一條（見其 `proposal.md` 的 Impact；delta 尚未寫入，目標是 `openspec/specs/admin-knowledge-reports-ui/spec.md:21` 的「使用者回報時 SHALL NOT 被要求附上來源 URL」），兩份 MODIFIED 打同一個 Requirement 會在 archive 時互相覆蓋。本 change 的介面要求一律以 ADDED 表達
