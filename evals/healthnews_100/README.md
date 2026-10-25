# CARE Healthnews 100 題測試交接

整理日期：2026-10-25。這裡保存之前的 100 題題庫、600 筆執行結果，以及當時使用的程式與設定。此次整理沒有重新呼叫模型，也沒有把待審查的結果改成通過。

## 先看哪個檔案

| 檔案 | 內容 |
| --- | --- |
| [CARE_Healthnews_100_Review_v3.json](CARE_Healthnews_100_Review_v3.json) | 完整 100 題，HN001–HN100；88 題單題、12 題複合題 |
| [answers/](results/20261014-live-batch/answers/) | 可直接閱讀的逐題回答，依 B/D 組與輪次分成 6 份 JSONL，每份 100 筆 |
| [initial-analysis.txt](results/20261014-live-batch/initial-analysis.txt) | 2026-10-14 留下的結果初步分析 |
| [initial-analysis.json](results/20261014-live-batch/initial-analysis.json) | 原始分析的結構化統計 |
| [full-results.tar.gz](results/20261014-live-batch/full-results.tar.gz) | 完整原始結果，包含 runs.jsonl、manifest.json、corpus_verification.json |
| [source-and-config.tar.gz](results/20261014-live-batch/source-and-config.tar.gz) | 當時的 code/ 與 experiment/，包含執行程式、app 原始碼、相依鎖定、題庫與實驗設定 |
| [manifest.json](results/20261014-live-batch/manifest.json) | 原始實驗版本、設定、模型、資料基準與完成筆數 |
| [corpus_verification.json](results/20261014-live-batch/corpus_verification.json) | 執行前後資料基準的核對紀錄 |
| [artifact-manifest.json](artifact-manifest.json) | 本次交接檔案的大小、SHA-256、完整性核對與回答節錄欄位 |

`answers/` 保留原本的題目、回答、來源、拆題、執行異常與審查狀態；沒有重寫或截短 `care_output`。詳細檢索證據、prompt 與階段事件仍完整保存在 `full-results.tar.gz`，沒有因為產生可讀版本而刪除。

題庫與使用者 Downloads 的 `CARE_Healthnews_100_Review_v3.json` 逐位元組一致，SHA-256 為 `49180741cbe53248876acdf9b45584022e1959ffda1fedd271b2b738b260c34a`。題庫內的 `draft_not_run` 是保留的原始標記；實際執行結果另外存放。實驗設定中的 `user_attested_complete` 記錄當時使用者確認題庫審核完成，並不代表 600 筆回答已通過審查。

## 這次測什麼

使用同一份 100 題，比較新知識庫下的 B 組（不拆題）與 D 組（拆題），每組各跑 3 輪，共 600 筆結果。這是直接進入 RAG 的實驗；完整 Agent 路由、急症分流與 LINE 操作需要另外驗證。

[Agent trace 交接](../agent_trace/README.md) 的單題測試用來確認 LangSmith 流程追蹤完整上傳，與這份 100 題的目的不同。兩份資料現在都收進 repo。

原先規劃的 A/C 舊知識庫組因缺少完整舊快照沒有執行，不能用這批 B/D 結果推論知識庫擴充的效果。

## 已有結果與限制

| 組別 | 總筆數 | 無最終失敗碼 | KB_EMPTY | MODEL_REFUSE | 有執行異常的筆數 |
| --- | ---: | ---: | ---: | ---: | ---: |
| B：不拆題 | 300 | 85 | 213 | 2 | 140 |
| D：拆題 | 300 | 114 | 184 | 2 | 138 |
| 合計 | 600 | 199 | 397 | 4 | 278 |

600 筆中，322 筆標記 `completed`，278 筆標記 `completed_with_incidents`。無最終失敗碼不等於答案正確或沒有中途異常；全部 600 筆 `review_status` 仍是 `pending`。答案正確性、完整性與引用支持尚待逐題審查，不能將兩組的回答比例差異當成醫療正確率提升。

當時有檢索、Jev 與 Cohere 的逾時或連線異常；網搜補救關閉。`KB_EMPTY` 只表示這次沒有取得可放行的證據，不能據此斷定整個資料庫沒有資料。

資料模式為 `live_observed`。紀錄顯示執行前後資料基準相同，但不證明中間從未變動。後續低並行診斷是另一份實驗；此處封存的是原本這批 600 筆，沒有混入其他設定下的重跑結果。

## 讀取完整紀錄

原始 `runs.jsonl` 是 **138,465,184 bytes**，約 138.47 MB／132.05 MiB；保留的原始壓縮包是 **38,894,729 bytes**，約 38.89 MB。這是完整紀錄的壓縮，不是只留下成功題或刪掉證據。

在 repo 根目錄執行：

```bash
mkdir -p /tmp/care-healthnews-results-20261014
tar -xzf evals/healthnews_100/results/20261014-live-batch/full-results.tar.gz \
  -C /tmp/care-healthnews-results-20261014
wc -l /tmp/care-healthnews-results-20261014/runs.jsonl  # 600
sha256sum /tmp/care-healthnews-results-20261014/runs.jsonl
```

解壓後的 SHA-256 可與 `artifact-manifest.json` 的 `full_results.uncompressed_sha256` 核對。原始三個結果檔與壓縮包內成員均已核對一致。

## 當時的執行程式與重跑條件

執行入口是封存包中的 `code/scripts/healthnews_batch_experiment.py`，相關模組在 `code/scripts/healthnews_batch/`。本次核對了設定中凍結的 294 個原始碼／相依檔雜湊，均與封存包相符；題庫與設定也與原實驗一致。保留歷史程式快照，讓結果能對回當時的版本。

可以先做離線預檢，不需要金鑰，也不提交模型工作：

```bash
mkdir -p /tmp/care-healthnews-source-20261013
tar -xzf evals/healthnews_100/results/20261014-live-batch/source-and-config.tar.gz \
  -C /tmp/care-healthnews-source-20261013
cd /tmp/care-healthnews-source-20261013/code
python3 scripts/healthnews_batch_experiment.py preflight --config ../experiment/experiment.json
```

預期為 `STATIC_READY`、600 筆計畫工作、0 筆模型提交。這只核對程式、題庫及設定，沒有驗證現在的資料庫或外部服務。

完整重跑還需要相依環境、原本使用的資源與模型、環境金鑰，以及可核對的資料基準；這個封存包不包含資料庫、憑證或用來續跑的 `journal.sqlite`。若程式或資料已改變，應建立新的實驗設定與輸出目錄，先完成實體資料預檢，才能執行明確要求 `--submit` 的付費工作。不要把原設定套在現在的程式上冒稱重現，也不要覆寫這批歷史結果。

接手後先審查既有異常與逐題證據，再決定後續診斷及重跑範圍。
