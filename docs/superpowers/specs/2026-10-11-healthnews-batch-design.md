# CARE v3 Batch 實驗設計

已核准範圍：完成實驗準備與最終整理，不提交正式工作。

## 流程與隔離

使用既有 ObservedRagAnswerService 與 run_case。BatchModel 適配 LangChain ainvoke/with_structured_output，
把相同 prompt、schema、thinking 設定送入 Gemini Batch，等待 future；coordinator 合併當前可用請求，按模型提交及輪詢。
每筆 run 以 config_version + case + arm + round 識別，模型請求另含 role、occurrence 與有效輸入 hash。
三輪不可共用回覆；同一筆未完成 run 的續跑允許使用該 run 已完成的階段收據，避免再次付費。
本機檢索、精排與 Jev 收據也按同一 run 保存，續跑不得重新取得不同證據。完成 run 直接載入已保存結果。

SQLite 保存設定 identity、model requests、jobs、階段收據與 results。先記提交意圖再呼叫 provider；
提交成功但 job ID 未持久化的窗口標為 unknown，停止自動重送，提供明確 attach-job reconciliation。
取消未提交請求不提交；已提交取消只取消本機等待，仍回收 provider 使用量，標 adopted=false。
啟動時拒絕設定／程式／題庫 hash 改變；輸出檔由持久結果重建，損毀末行不影響 journal。

## 執行設定

模型：decomposition/rewrite/crag fallback=gemini-3.8-flash（low），generation=gemini-3.5-flash-lite（default）；
embedding=gemini-embedding-001（3072），Cohere rerank-v4.0-pro、Jev jev-1.13.0 first。
web=false、答案 cache=false、writeback=false；每 run 新 link checker。保留投機生成／改寫。
並行上限 24；每 job 最多 100 請求；輪詢 30 秒。Batch queue latency 分開呈現。
預先固定每 run 最多 160 個融合候選、生成 context 上限 16384 token；超限標為 budget_exceeded，
不靜默截斷或改變醫療條件。保留每路 40 候選／生成最多 5 片段，實際資源另記。

## 快照與驗收

新庫快照 CLI 對 Mongo 文件與 PG 向量做固定複本，保存一致 ID、內容雜湊、索引定義與實體就緒。
預設 plan/check；create 是明確外部副作用。拒絕覆寫 target/live；來源複製中變動則不標 verified。
目前沒有完整 old 快照，A/C 持續 blocked，不從新增資料備份推造。
實驗前後實讀新快照 hash；檢查索引定義及 queryable；規格審核與實驗結果審核分開。

不足模板與三來源上限是既有待審問題，此次保留 CARE 實際輸出，同時匯出阻擋欄位／引用映射以供逐題審查，
不在 Batch adapter 內偷偷修正回答。保存 reference/claim/subquestion review 空欄與 origin，醫療判定不自動推造。
