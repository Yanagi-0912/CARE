# CARE v3 離線 Batch 實驗

此入口復用 CARE 的 RAG 判斷與 prompt，將 Gemini 請求改由 Batch 執行。正式執行器為
`scripts/healthnews_batch_experiment.py`；獨立 Job 使用同一 SDK 映像，程式與結果放在持久 hostPath。
不需要修改 LINE／API 服務的 ConfigMap 或啟用線上拆題。

## 本次設定

題庫 100 題、B/D 各三輪，共 600 筆結果；A/C 等完整 old 快照。題庫原文、metadata 保留，
人工審核完成依使用者陳述另記 attestation。模型／來源檔案 hash／快照內容及索引定義固定在 `experiment.json`。
候選每路 40、每 run 融合候選總上限 160、生成最多 5 片段、context 上限 16384 token；超限停止該筆，
不截斷必要條件。各組實際資源量仍可能不同，需要和效果一起審查。

Gemini analysis（拆題、改寫、fallback）使用 gemini-3.8-flash low；生成使用 gemini-3.5-flash-lite default。
Embedding、Cohere、Jev 仍由即時 API 執行；Gemini 批次不是將整個 RAG pipeline 送給 Gemini。
依賴固定為專案 uv.lock 及部署映像 digest；來源 freeze 包含所有 app Python、離線入口與釘選 CA 憑證。

## 預檢與執行

以下在包含凍結程式與既有環境金鑰的實驗環境中執行，`CONFIG` 指向正式設定檔：

```bash
python -B scripts/healthnews_batch_experiment.py preflight --config "$CONFIG"
python -B scripts/healthnews_batch_experiment.py preflight --config "$CONFIG" --verify-snapshot
python -B scripts/healthnews_batch_experiment.py status --config "$CONFIG"
python -B scripts/healthnews_batch_experiment.py run --config "$CONFIG" --submit
```

預設只預檢；`run` 沒有 `--submit` 會在建立任何 provider 前拒絕。正式 run 必須實讀快照 hash、ID、
Atlas definition/queryable、PG primary/HNSW/dimension。結束後再核對。`preflight` 不提交模型工作；
單純金鑰存在不等於即時供應商或正式 Batch 完整 RAG 成功。

API key、Mongo URI、PG DSN、Jev/Cohere key 沿用既有 Secret 注入，不寫入 experiment.json。
快照 PG 連線可使用 `PGVECTOR_SNAPSHOT_DSN`，其後是既有 `PGVECTOR_SYNC_DSN`，最後才是 `PGVECTOR_DSN`。
無論角色是否可寫，離線檢索連線都以 default_transaction_read_only=on 建立。未擴大 care_app 的既有 grants。

## 快照建立及索引配額

```bash
python -m scripts.healthnews_snapshot plan --out "$SNAPSHOT_DIR"
python -m scripts.healthnews_snapshot create --out "$SNAPSHOT_DIR"
python -m scripts.healthnews_snapshot check --out "$SNAPSHOT_DIR" --polls 1
python -m scripts.healthnews_snapshot create-index --out "$SNAPSHOT_DIR"
```

預設 target 是 `healthnews_exp_20261011_new` 與 `healthnews_exp_20261011.health_articles_chunks`；
create 拒絕任何存在目標／manifest。`check` 只讀 DB；可以證明原始來源與複本 hash 一致後恢復失敗紀錄。
`create-index` 只建立指定實驗 target 的缺少索引，不改現有索引。Postgres schema 若角色無建立權限，
需先建立僅供本實驗的 schema；工具不變更任何 grants。

本次新庫文件／向量已複製；Atlas 全域三個搜尋索引已用滿，新文字索引回報 code20 容量限制。
保留資料與匯出，狀態停在 pending；不得將 data_ready 當作 verified_immutable。
解除配額並完成 create-index/check 後，更新正式 manifest 與 config freeze，才能執行。
不得直接使用 live BM25 配固定向量快照，或偷偷移除 user_document_chunks_vector。

## 可續跑與不明結果

SQLite journal 固定 config_version + case + arm + round，每個階段另有有效輸入 hash 和 occurrence。
完成的 run 不重送；未完成的同一 run 可重用已發生的本輪階段收據；不同輪次／組別完全獨立。
設定、題庫或程式 hash 改變需使用新的實驗目錄，不能把舊 journal 當成新設定的續跑。

provider.create 前先持久化意圖；呼叫結果不明時停在 SUBMIT_UNKNOWN。須從供應商查出正確 job name，
以 `reconcile --job-id LOCAL_ID --provider-job-name batches/NAME --config "$CONFIG"` 綁回。
不得盲目刪除 journal 或重新建立工作。反序回應以 key 配對，missing/duplicate/malformed results 拒絕。

即時 retrieval/embedding/Cohere/Jev 也先寫 started 收據；中斷或重開仍無結果時標 unknown，
停止後續付費呼叫。只有取得可核對的原始結果，才使用 `reconcile-receipt --run-key ... --stage ...
--receipt-file recorded-result.json --config "$CONFIG"`；JSON 需含相同 input_hash、done/error status 與 payload。
token-count 與來源連結檢查是可重試讀取，取消／重試另記，不視為新的模型答案。真正 Jev 2 秒逾時保存為
TimeoutError 並保留既有 Gemini fallback；程序中斷不冒稱逾時。

未送出的投機請求取消後不提交；已提交取消只取消本機等待，仍回收 response／usage。
不因本機取消而宣稱 provider 未計費。Batch 等待排除互動式 45 秒 timeout，離線 wall time 不當作 LINE 延遲。

## 保存與審查

`output/journal.sqlite` 是權威紀錄；`runs.jsonl` 可從 journal 重建，寫入使用 atomic rename。
紀錄有效 grader prompt／截斷、完整 evidence、生成 context、引用映射、錯誤、取消、token 使用量与審查空欄。
`claim_reviews`、`subquestion_reviews`、expected CRAG 都需逐題獨立審查，不能由模型輸出自動填成通過。

KB_EMPTY 模板和最多三來源導致的引用呈現問題仍以 CARE 實際輸出觀察，標 blocking/review 欄位；
本入口未偷偷重写醫療回答。RAG 直接入口不能驗證完整 Agent 急症分流。無完整 old 快照，不能歸因資料庫擴充效果。
操作 Job 前須完成最終 READY 預檢；本次交付並未提交正式實驗。
