# CARE 實驗總交接

核對日期：2026-10-25。範圍是這次討論的 **Healthnews 100 題研究與 Agent／LangSmith 流程追蹤**：本機 `experiments/` 下 8 個實驗目錄，以及相關 VM 上已完成的結果與執行 journal。本次是資料交接與狀態核對，沒有重新執行付費實驗。

本機共 **6,765 個非快取檔案**逐一記錄在 [inventory.json](inventory.json)，每個檔案都有原位置、大小、SHA-256 與交接位置。相同內容若已在 repo 或封存包中，就指向那份資料；250 個可重建的 Python 快取未封存。不同設定的實驗保留各自結果，沒有合併成同一份成效統計。

## 從哪裡開始

- [100 題題庫與第一批 600 筆結果](../healthnews_100/README.md)
- [後續低並行重跑的 600 筆回答](cloud/controlled-study/controlled/answers/)、[統計](cloud/controlled-study/controlled/summary.json)
- [完整診斷流程的最終狀態](cloud/controlled-study/sequence-status.json)
- [Agent／LangSmith 單題交接與驗收](../agent_trace/README.md)
- [本次完整性核對](verification.json)、[檔案雜湊清單](artifact-manifest.json)

## 實驗時間線與交接狀態

| 日期／實驗 | 已有內容 | 執行與審查狀態 | 交接入口 |
| --- | --- | --- | --- |
| 10/10：100 題前期試跑 | 8 題 × B/D × 3 輪＝48 筆，另有 3 題 × B/D＝6 筆驗證；逐題分析、程式差異、設定與測試紀錄 | 共 54 筆試跑；不可替代正式成效審查 | [pilot 封存](archives/healthnews-100-20261010.tar.gz) |
| 10/11：Batch API 探測 | 拆題與生成各 1 次，含請求、回應、usage 與驗證 | 2 次傳輸探測成功；不是 2 筆完整 RAG 實驗 | [Batch 探測封存](archives/healthnews-batch-20261011.tar.gz) |
| 10/11：本機／雲端就緒性核對 | 版本差異、來源引用、Mongo／PG／LangSmith 設定與唯讀探測 | 當時發現的阻擋與限制保留，不當作現在仍未解決的狀態 | [就緒性報告封存](archives/readiness-20261011.tar.gz)、[本機設定封存](archives/local-readiness-20261011.tar.gz) |
| 10/11：固定新快照 Batch 準備 | 程式、設定、Job、快照與索引配額紀錄 | 這份設定未啟動正式批次；後來另建 10/13 的 live 模式。A/C 舊庫組仍未執行 | [固定快照準備封存](archives/healthnews-formal-batch-20261011.tar.gz) |
| 10/13：第一批 B/D 正式執行 | 100 題 × B/D × 3 輪＝600 筆；完整證據、設定、程式快照與 journal | 322 筆 completed、278 筆有執行異常；600 筆品質審查 pending | [原始 600 筆](../healthnews_100/README.md)、[journal](cloud/original-600/)、[準備與啟動紀錄](archives/healthnews-live-batch-20261013.tar.gz) |
| 10/14：低並行／較高並行診斷 | 固定 8 題 × B/D；並行 1、並行 4 各 16 筆，共 32 筆 | 兩階段均無執行異常；小樣本僅驗證基礎設施，品質審查仍 pending | [並行 1](cloud/controlled-study/low/)、[並行 4](cloud/controlled-study/higher/) |
| 10/14–15：受控低並行重跑 | 同一份 100 題 × B/D × 3 輪＝另一批 600 筆 | 598 筆 completed、2 筆有執行異常；全部品質審查 pending，最終狀態為品質效果評估受阻 | [重跑結果與 journal](cloud/controlled-study/controlled/)、[程式／設定](cloud/controlled-study/source-and-config.tar.gz)、[診斷準備紀錄](archives/healthnews-controlled-20261014.tar.gz) |
| 10/14–16：Agent／LangSmith | 本地執行程式、單題案例、流程圖、歷史 trace 與 10/16 驗收 | 10/16 單題六項驗收通過；不等於 100 題或全部 Agent 功能通過 | [Agent 交接](../agent_trace/README.md)、[早期補充資料](archives/langsmith-agent-first-look-20261014.tar.gz) |

歷史封存內可能有 `NOT_STARTED`、`DIAGNOSTICS_RUNNING` 等當時的紀錄。後續診斷的最終結果應看 `cloud/controlled-study/sequence-status.json`，不能只看 10/14 的啟動紀錄。

## 這次找回的後續結果

原本本機只有診斷的啟動紀錄；本次從 VM 取回最終輸出並與 journal 核對：

| 階段 | 筆數 | 無最終失敗碼 | KB_EMPTY | MODEL_REFUSE | 有執行異常 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 並行 1 診斷 | 16 | 9 | 7 | 0 | 0 |
| 並行 4 診斷 | 16 | 9 | 7 | 0 | 0 |
| 低並行重跑 | 600 | 274 | 319 | 7 | 2 |

低並行診斷通過後，流程才啟動完整重跑；重跑最終狀態為 `CONTROLLED_COMPLETED_WITH_INCIDENTS_QUALITY_EVALUATION_BLOCKED`。這表示已完成資料產出，但仍有 2 個技術異常，不能宣稱實驗品質驗收通過，也不能單靠回答比例宣稱拆題改善醫療正確率。

每個階段都提供 `full-results.tar.gz`、`journal.sqlite.gz`、原始 manifest／資料核對／diagnostics、可直接閱讀的回答及 `summary.json`。完整輸出沒有刪掉失敗題，回答節錄保留原文。

## 原始紀錄與 journal

兩批 600 筆的檔案不同：

- 原始批次 `runs.jsonl`：138,465,184 bytes；journal：209,567,744 bytes。
- 後續低並行重跑 `runs.jsonl`：183,367,317 bytes；journal：277,647,360 bytes。

接近 300 MB 的 journal 確實存在，已壓縮納入交接。壓縮只改變儲存方式，解壓後與雲端原檔 SHA-256 相同。見 [下載核對](cloud/download-verification.json) 與各階段 `summary.json`。

journal 保存結果、模型請求、Batch 工作與階段收據。已對 4 份 journal 執行 SQLite 完整性檢查，並逐筆比對 `results` 表與匯出的 JSONL。這些是封存紀錄，查看時應使用唯讀模式；不要直接啟動舊 runner 來「看看狀態」，避免改動封存 journal。

例如在 repo 根目錄解壓重跑 journal：

```bash
gzip -dc evals/experiment_handoff/cloud/controlled-study/controlled/journal.sqlite.gz \
  > /tmp/care-controlled-20261014-journal.sqlite
python3 - <<'PY'
import sqlite3
with sqlite3.connect('file:/tmp/care-controlled-20261014-journal.sqlite?mode=ro&immutable=1', uri=True) as db:
    print(db.execute('SELECT count(*) FROM results').fetchone()[0])  # 600
PY
```

## 檔案清單怎麼看

`inventory.json` 的 `local_directories` 對應原本的 8 個目錄；每個 `files` 項目都提供 `handoff_location`：

- `file`：內容已在指定的 repo 路徑。
- `archive_member`：在指定壓縮包內的 `member` 路徑。
- `existing_git_file`：與指定 commit 的檔案完全一致，例如部署快照中已在 repo 的圖片與程式；保留 commit 與 SHA-256，避免未來版本改變後無從對照。

因此 `archives/` 是補充封存，不保證單獨解壓某一包就還原整個原目錄；完整對照以 `inventory.json` 為準。正式兩批結果的 `full-results.tar.gz` 與程式設定封存則各自保留完整的原始結構。

## 外部資料與接手條件

實驗用資料庫快照仍保存在 VM `care-vm` 的 `/var/lib/care-experiments/healthnews-v3-batch-20261011/snapshot-exports/`：Mongo 匯出、PG 向量匯出與快照 manifest。它們是外部資料依賴，沒有放進 Git；已在 [運維與快照紀錄](cloud/operations-and-snapshots-20261025.json) 記錄位置、大小、SHA-256 與 manifest，取得時需要該 VM 的存取權。

實際重跑還需要對應資料基準、環境金鑰、模型與 LangSmith workspace 權限。金鑰不進版控；原本缺少的舊知識庫快照也沒有被新快照取代。當程式、資料或模型版本改變時，須建立新的實驗身份與輸出位置，不能覆寫歷史結果或直接重送已完成工作。

2026-10-25 唯讀查核：`care-etl` 的 `suspend=false`，兩次實驗的恢復紀錄皆為 `RESUMED`。本次沒有變更排程、資料庫、線上設定或模型工作。

## 尚未完成的研究工作

資料交接完成不代表研究驗收完成。仍需處理受控重跑的 2 個技術異常，並逐題審查醫療正確性、引用支持與回答完整性；A/C 舊庫對照尚未執行。直接 RAG 的測試也不能替代完整 Agent／LINE 流程驗證。這些待辦及所有失敗紀錄均保留在交接內容中。
