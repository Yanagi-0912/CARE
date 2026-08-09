## Context

`KnowledgeReportRepository.update` 是整份文件 `$set`（`model_dump` 後全欄位寫入）。同步 ingest 時代這不成問題，因為 approve 從讀到寫是一個 request 內完成、沒有其他人插手的空檔。改成背景執行後，`run_ingest` 手上的 report 物件是工作開始時的快照，收尾時整份寫回會覆蓋這段期間任何其他寫入。

前端佇列篩選（`visibleReports`）是在 `rawReports` 上做 client-side 過濾，這在「一次載入全部」時等價於完整篩選；改成分頁後就只剩「對已載入頁過濾」。

## Goals / Non-Goals

**Goals:**
- 背景 ingest 不得覆蓋期間內的其他狀態變更
- 拒絕過的回報，其內容不得因為背景工作而進入向量庫
- 篩選後的分頁資料完整可達
- 重試不需要 admin 重打已經輸入過的 URL
- ingest 完成後畫面自行更新

**Non-Goals:**
- 改用 cursor 分頁（仍為 offset；只處理重複 key 的症狀）
- 分散式鎖或外部 job queue
- 已進入向量庫的 chunk 的回收／反收錄
- 已結案（resolved／rejected）的計數；只涵蓋待審佇列的 pending／reviewing

## Decisions

1. **以條件式更新取代整份 `$set`，範圍限縮在 ingest 路徑。**
   新增兩個 repository 方法，兩者都回傳是否命中：
   - `start_ingest_job(report_id, job, status, resolution, reviewer_note, stale_before)`：filter 同時表達「未結案」與「沒有進行中的新鮮工作」，命中才寫入。取代 approve 的 read-then-write，順帶解掉併發重複啟動。
   - `finish_ingest_job(report_id, started_at, ...)`：filter 綁 `ingest_job.started_at` 等於本次工作的開始時間且 `ingest_job.status` 仍為 `running`。若期間被拒絕或被重新 approve，filter 不命中，本次結果直接丟棄。
   只寫 ingest 相關欄位（`status`、`ingest_job`、`updated_at`），不碰 `reviewer_note` 等其他欄位。
   `update` 保留給 `reject` 等單一 request 內完成的路徑，不動它。

2. **`reject` 在 ingest 進行中回 409，而不是讓它贏。**
   即使寫入層已經安全，讓 reject 贏仍會留下「狀態是 rejected 但 chunk 已在向量庫」的狀態，而系統沒有反收錄能力。擋住是唯一能保證資料一致的做法。逾時（`INGEST_JOB_STALE_AFTER`）之後解除，與 approve 用同一個判斷。

3. **`approve` 的備註採 patch 語意**：`resolution`／`reviewer_note` 為 `None` 時保留原值，有帶值才覆寫。重試不帶備註即視為「沿用上次的」。清空備註不在本 change 支援（需要與「不帶」區分，得引入 sentinel，不值得）。

4. **篩選改 server-side。**
   `activeFilter` 進 query key，`all` 不帶 `status`（後端預設 pending＋reviewing），其餘帶對應值。換頁籤即換一條 query、分頁從頭算，「載入更多」永遠對應當前篩選。
   代價：頁籤與 hero 統計不能再從已載入的頁自己算（那只反映已載入的部分）。解法是**後端一併回 pending／reviewing 的實際筆數**（`status_counts`），與篩選條件無關恆定回傳，`all` 即兩者之和。多兩次 `count_documents`，在新的 `status` + `created_at` 索引下是 covered count，成本可忽略，換到的是所有數字都準。

5. **重試的選取來源優先取 `ingest_job.selected_urls`。**
   那份清單就是上次實際送出的 URL（含 admin 補的）。`openDialog` 時若 report 有 `ingest_job`，以它為選取內容，其中不屬於 `user_source_urls` 的自動歸入 `extraUrls` 以便顯示「審核者補充」。

6. **輪詢條件化**：`refetchInterval` 在任一已載入回報 `ingest_job.status === 'running'` 時為 5 秒，否則 `false`。不改全域 `staleTime`，避免影響其他頁面。

7. **去重放在 flatten 時**：`report_id` 進 `Set`，重複的丟棄。修掉 React 重複 key 與畫面重複列；offset 分頁「漏一筆」的情形仍存在，維持既有取捨。

## Risks / Trade-offs

- **[reject 被 ingest 擋住]** → admin 最久等 10 分鐘或等工作失敗。相對於「拒絕了但內容還是進了知識庫」，這個代價可接受，且錯誤訊息會說明原因。
- **[條件式更新讓 run_ingest 的結果可能整份丟棄]** → 這正是目的；被丟棄代表期間有人改過狀態，該以後者為準。丟棄時不重試、不報錯。
- **[頁籤數字變少]** → UI 資訊量下降。用「顯示不誤導的數字」換「不顯示誤導的數字」。
- **[offset 分頁仍會漏列]** → 去重只解重複，不解遺漏。維持 `admin-review-queue-hardening` 的原判斷。

## Migration Plan

無 DB migration。新複合索引由 `ensure_indexes` 於啟動時建立，`create_index` 具冪等性。

## Open Questions

- （無）
