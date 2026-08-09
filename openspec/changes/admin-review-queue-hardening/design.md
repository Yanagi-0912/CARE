## Context

四個缺口彼此獨立但都落在同一條路徑上（admin 列表端點 → API client → 審核頁 dialog），拆成四個 change 會讓同一個檔案被改四次。合併為一個加固型 change，不動既有語意。

實作跨兩個 repo：後端 `CARE`，前端 sibling `/Users/jamessu/Desktop/computersciencehomework/CARE-LIFF`。

## Goals / Non-Goals

**Goals:**
- admin 能只核准信得過的那幾個 URL
- ingest 失敗在畫面上看得見、看得懂、可重試
- 非法 `status` 回 422 而非假裝空佇列
- 列表可分頁，不隨資料量線性膨脹

**Non-Goals:**
- resolved／rejected 歷史查詢頁（本 change 只動待審佇列）
- 自動重試、輪詢即時更新（重試為手動觸發）
- 游標式分頁

## Decisions

1. **`status` 驗證交給 FastAPI**：`Optional[KnowledgeReportStatus]`（既有 `Literal`）當 Query 型別，非法值自動 422，不手寫檢查。

2. **分頁用 `limit`／`offset`，不用 cursor**。待審佇列量級小、且 UI 需要顯示總數與跳頁；cursor 的穩定性優勢在這裡用不到。`limit` 以 `Query(default=50, ge=1, le=200)` 約束，上限由框架擋。

3. **分頁欄位加在既有 `KnowledgeReportListResponse` 上且為 optional**，不另開 admin 專用 model。使用者端不填 → 序列化為 `null`，前端既有型別不破。代價是 response model 語意稍鬆，換到的是兩端共用一個 DTO。

4. **`count_by_statuses` 獨立一次查詢**，不用 `$facet` 聚合。兩次簡單查詢在這個資料量下更好讀，也更好用 mock collection 測。

5. **URL 勾選預設全選**：維持目前「一鍵核准」的手感，想縮減範圍才需要動手。全部取消勾選時停用核准鈕（後端的空 `selected_urls` 會 fallback 成全部，前端必須擋在前面，否則會做出與畫面相反的事）。

6. **Admin 可自行補來源 URL。** `user_source_urls` 在 LIFF 表單與 `submit_knowledge_report` tool 兩邊都是選填，使用者主動回報「這裡不對」時通常不附來源；只有 web fallback 自動建的回報保證有 URL。若只能從 `user_source_urls` 勾選，這類回報將永遠無法核准、只能被退掉，等於把使用者主動回報這條路廢掉。故 dialog 提供輸入框讓 admin 補上自己查到的權威來源，與使用者提供的來源併入同一份勾選清單。
   後端無須改動：`approve` 本就接受任意 `selected_urls`，且逐一跑 `is_allowed_url`，admin 補的網址一樣受白名單約束。

6. **重試就是再送一次 approve**，前端只是把按鈕文案換成「重試」，帶當前勾選的 URL。後端 409（進行中）的訊息直接顯示在 dialog 的錯誤區。

7. **ingest 狀態呈現**：卡片層級只放狀態標記（進行中／失敗），詳情 dialog 才展開逐 URL 的 `status`／`chunk_count`／`message` 與 job 層級 `error`。避免列表被錯誤訊息塞爆。

8. **分頁 UI 用「載入更多」而非頁碼**：佇列是時間序、admin 由新往舊掃，append 比跳頁貼近實際操作。`total` 仍顯示以便知道剩多少。

## Risks / Trade-offs

- **[offset 分頁在資料變動時會漏／重複]** → 待審佇列在 admin 審核時本來就會變動。以「載入更多」呈現、每次操作後重載第一頁，可接受。
- **[勾選狀態與 dialog 生命週期]** → dialog 關閉需重置勾選，否則換一筆回報會沿用上一筆的選取。closeDialog 一併清除。
- **[optional 分頁欄位讓 response 契約變鬆]** → 以 proposal 明示只有 admin 端會填，並在前端型別標為 optional。
- **[相依 `knowledge-report-async-ingest`]** → 若該 change 未先落地，`ingest_job.status` 不存在，UI 只能靠 `error` 是否為空推斷。故排在其後執行。

## Migration Plan

無 DB migration。分頁參數皆有預設值，未帶參數的既有呼叫行為等同「第一頁 50 筆」——這是相對現況（全撈）唯一的行為變化，且前端會同步改為分頁載入。

## Open Questions

- （無）
