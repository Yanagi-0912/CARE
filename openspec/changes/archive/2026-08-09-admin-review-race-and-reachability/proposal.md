## Why

`knowledge-report-async-ingest` 把 ingest 移到背景後，approve 到 ingest 完成之間出現了一段先前不存在的時間窗；`admin-review-queue-hardening` 把列表改成分頁後，既有的前端篩選從「對全部資料過濾」變成「對已載入的頁過濾」。code review 在這兩處各找出實質缺陷：

1. **背景 ingest 會蓋掉中途的拒絕**：repository 的 `update` 是整份文件 `$set`，而 `reject` 不擋 `reviewing`。admin 在 ingest 途中按拒絕會成功，但 ingest 收尾時把整份舊快照寫回去——狀態變回 `resolved`、拒絕備註被清空，且內容已進向量庫。
2. **篩選頁籤會讓後續分頁不可達**：「載入更多」寫在列表分支內，篩選在已載入頁上無命中時走空狀態分支，按鈕隨之消失。第一頁若剛好沒有該狀態的回報，後面所有符合的資料都拿不到，畫面還顯示「目前沒有待審回報」。
3. **重試對 admin 補的 URL 無效**：`openDialog` 只從 `user_source_urls` 種選取，不看 `ingest_job.selected_urls`。無來源回報由 admin 補 URL 後 ingest 失敗，重開詳情時選取清單是空的，重試鈕停用。
4. **重試會清掉前次審核備註**：`approve` 無條件覆寫 `resolution`／`reviewer_note`，而重試請求不帶備註。
5. **併發 approve 可重複啟動 ingest**：進行中判斷是 check-then-write，兩個請求可同時通過並各排一個背景工作，重複灌入 chunk。
6. **分頁查詢無對應索引**：`ensure_indexes` 沒有 `status` + `created_at`，admin 查詢為全集合掃描加記憶體排序。
7. **「收錄中」不會自己結束**：全域 `staleTime` 30 秒且關閉 focus refetch，又未設輪詢，非同步 ingest 完成後畫面不會更新。
8. **分頁邊界重複列**：offset 分頁在佇列變動時可能重複取得邊界那筆，造成 React 重複 key。

## What Changes

- **後端**
  - repository 新增條件式更新：`start_ingest_job`（原子地登記工作）與 `finish_ingest_job`（僅在工作仍屬本次執行時寫回結果），取代 approve／run_ingest 路徑上的整份 `$set`
  - `reject` 於 ingest 進行中回 409，避免拒絕後內容仍進入向量庫
  - `approve` 僅在請求有帶值時才覆寫 `resolution`／`reviewer_note`
  - `run_ingest` 的例外路徑保留已收集的逐 URL 結果
  - `ensure_indexes` 新增 `status` + `created_at` 複合索引
- **前端（CARE-LIFF）**
  - 佇列篩選改為送 `status` 給後端並納入 query key，分頁在篩選後仍完整可達
  - `openDialog` 以 `ingest_job.selected_urls` 為優先種入選取，重試可直接沿用上次的 URL
  - 任一已載入回報的 ingest 仍在進行中時啟用輪詢，完成後自動停止
  - 合併分頁結果時依 `report_id` 去重

## Capabilities

### Modified Capabilities

- `knowledge-reports`：核准／拒絕與背景 ingest 的併發規則；分頁查詢的索引需求
- `admin-knowledge-reports-ui`：篩選與分頁的互動、重試的選取來源、ingest 進行中的更新

## Impact

- **CARE**：`app/repositories/knowledge_report_repository.py`、`app/services/knowledge_reports/service.py`
- **CARE-LIFF**：`src/pages/AdminKnowledgeReports/index.tsx`
- **測試**：`tests/unit/services/knowledge_reports/test_service.py`、`tests/unit/repositories/test_knowledge_report_repository.py`、`src/tests/adminKnowledgeReports.test.tsx`
- **UI 變更**：篩選改 server-side 後，頁籤數字只對當前選中的頁籤有意義（來自後端 `total`），未選中的頁籤不再顯示已載入筆數
