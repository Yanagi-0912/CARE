## Context

`rag-eval-metrics` change 已建立可信量測基準（`python scripts/rag_eval.py --rank-mode cohere --top-n 5 --with-answer`，2026-08-08）：

- `hit_rate@5 = 0.412`
- `mean_mrr = 0.198`
- `mean_ndcg@5 = 0.253`
- `citation_coverage = 1.0`

`hit_rate@5` 有 0.412、`mean_ndcg@5` 只有 0.253，這個組合說明：相關文件多半撈得到（召回不是主要問題），但排序把它們壓到後面（前幾名品質不足）。本 change 針對造成排序品質差的兩個實測根因下手：

1. 向量檢索的 `DEFAULT_MIN_SCORE = 0.5`（`app/services/rag/retriever.py`）在候選送進 reranker 之前就先過濾掉一批，與 `cohere-rag-rerank` change 確立的「wide retrieve → rerank → top-n」架構意圖相反。
2. 上游 ETL（`Capoo0618/CARE-data` 的 `main_pipeline.py`）以 `f"主題：{title}\n內容：{chunk}"` 產生向量 embedding，但寫入 Mongo 的 `chunk_content` 欄位不含標題。`app/services/rag/cohere_reranker.py` 目前直接把 `doc.page_content`（即不含標題的 `chunk_content`）送給 Cohere Rerank API，BM25（hybrid 路徑）看到的也是同一份缺語境文本。三個階段（向量／BM25／rerank）看到的文本不一致，rerank 判斷相關性時失去了向量檢索原本擁有的標題語境。

## Goals / Non-Goals

**Goals:**

- 移除向量檢索第一階段的硬性相似度過濾，讓候選盡量完整地進入精排階段
- 讓送入精排的文件文本語境與 embedding 建立時一致，改善 Cohere Rerank 的排序品質
- 清除已知的 Firecrawl 導覽列噪音資料，避免噪音 chunk 佔用檢索候選名額
- 修正 BM25 索引範本欄位名錯誤，避免未來依範本建索引的人建出失效索引

**Non-Goals:**

- 不調整 `RAG_RETRIEVE_CANDIDATES`、`RAG_RERANK_TOP_N`、`RAG_RRF_K` 等既有候選數量／融合參數
- 不重寫上游 ETL、不對 `chunk_content` 補標題（見 D3）
- 不引入新的精排模型或供應商
- 不改變 `get_rag_answer` 對外 tool 介面與 LINE 回覆格式

## Decisions

### D1. 為何移除 min_score 而非調低

在 wide retrieve → rerank 架構下，第一階段的職責是最大化召回，過濾與排序是精排階段的工作；`0.5` 這個絕對門檻在候選送進 reranker 之前就先砍掉一批，與這個架構意圖相反。因此不是把 0.5 調低成另一個猜測值，而是移除硬門檻本身，讓候選盡量完整地交給精排去判斷去留。設定項 `RAG_VECTOR_MIN_SCORE` 保留（預設 `0.0`），使未來如果真的需要在向量階段做門檻過濾，仍可由環境變數調回非零值，而不必改程式碼。

（曾考慮以「hybrid 路徑經 RRF 融合後 `metadata["score"]` 已被覆寫為融合分數，對融合分數套用針對 cosine 設計的門檻語意錯誤」作為額外理由，但查證程式碼後該敘述不成立：`min_score` 過濾發生在 `MongoAtlasVectorRetriever.ainvoke` 內部（`retriever.py:122-124`），早於 `HybridRetriever` 呼叫 `reciprocal_rank_fusion`；RRF 融合分數覆寫只發生在回傳值上，其後沒有任何 min_score 檢查。故不採用這條理由。）

### D1b. 使用者上傳文件路徑不同步放寬

`app/services/rag/user_document_retriever.py` 目前 `from app.services.rag.retriever import DEFAULT_MIN_SCORE` 並直接用作 `UserDocumentVectorRetriever` 的預設 `min_score`，`app/dependencies.py` 組裝時也未覆寫。若本 change 只改共用的 `retriever.py` 常數，`user_document_retriever.py` 會跟著靜默把門檻改成 0.0。

但 D1 的前提「過濾交給精排」在使用者上傳文件這條路徑上不成立：`UserDocumentAnswerService` 沒有 reranker，`UserDocumentVectorRetriever` 的檢索結果直接進 prompt。拿掉門檻卻沒有任何機制補償，等於靜默拆掉這個功能既有的品質底線。

因此決定：兩條路徑各自持有明確、獨立的預設值，不再共用同一個常數。`app/services/rag/retriever.py` 的 `DEFAULT_MIN_SCORE` 改為 `0.0`（供有 reranker 把關的知識庫檢索路徑使用），`user_document_retriever.py` 新增自有的 `DEFAULT_USER_DOC_MIN_SCORE = 0.5` 並改用它作預設，不再 import 前者的常數。此變更範圍屬 Task 8。

### D2. reranker 文本格式對齊 embedding

送入 Cohere Rerank 的 document 文本刻意與上游 `main_pipeline.get_embedding` 的 `f"主題：{title}\n內容：{chunk}"` 格式完全一致（`主題：{original_title}\n內容：{chunk}`），使向量檢索、BM25（hybrid）、精排三個階段看到的語境盡量收斂到同一份文本。`original_title` 缺失時（例如來源資料本身沒有標題）退回純內容，不阻斷精排流程。

精排函式回傳的 `Document.page_content` 維持原始 `chunk_content`（不含標題前綴）不變——組出的「主題：…\n內容：…」文本只用於送給 Rerank API 判斷相關性，不影響下游 `_build_context` 組 prompt 或 `_append_sources` 顯示來源時使用的內容。

### D3. 為何不順手改 BM25 索引欄位補標題

在 `chunk_content` 補標題（即讓 BM25 索引也吃到 `主題：{title}\n內容：{chunk}` 格式）需要重寫全部 4,605 筆既有文件的內容欄位，這是資料層級的批次遷移，且 `chunk_content` 是外部 ETL repo（`Capoo0618/CARE-data`）每日寫入的欄位，屬上游職責範圍（詳見 `docs/care-data-issues.md`）。本 change 只在應用層（reranker 送出的請求文本）做語境補償，不改動資料庫既有欄位內容，範圍與風險都可控；資料層級的標題回填留待上游 ETL 或另一個 change 處理。

## Task 8 驗證結果：移除 min_score 後的 eval 對照

實跑指令：`python scripts/rag_eval.py --rank-mode cohere --top-n 5 --out /tmp/rag-c1.json`（2026-08-08，`RAG_VECTOR_MIN_SCORE=0.0`，即本 change 改動後的預設值）。

| 指標 | Baseline（`DEFAULT_MIN_SCORE=0.5`，rag-eval-metrics change 記錄） | 本次（`RAG_VECTOR_MIN_SCORE=0.0`） |
| --- | --- | --- |
| `hit_rate@5` | 0.412 | 0.4117647058823529（14/34，與 baseline 相同） |
| `mean_mrr` | 0.198 | 0.198 |
| `mean_ndcg@5` | 0.253 | 0.253 |

三項指標與 baseline **完全一致**（`miss_ids` 與 `skipped_ids` 清單也逐字相同）。查證：另外用同一版程式碼、同一份 golden set，臨時以環境變數 `RAG_VECTOR_MIN_SCORE=0.5` 還原舊行為重跑一次（見延遲量測），metrics 同樣完全相同。可見 `min_score=0.5` 這個門檻在這份語料 × Gemini embedding 的組合下**幾乎沒有實際過濾到任何候選**——vectorSearchScore 落在 0～0.5 之間的候選在這個資料集中很罕見（若確有存在，也未落在會影響 hit_rate/mrr/ndcg 的排名區間內）。這與 D1 的論證方向一致（移除門檻不會讓不相關的低分噪音大量湧入），但也說明**本 change 對這份 golden set 沒有帶來立即可測得的排序品質提升**；D1 的價值在於移除一個與架構意圖相反、且在其他語料分布或未來索引調整下可能悄悄丟資料的隱性機制，而非本次量到的分數改善。真正改善 ndcg 的手段是 D2（reranker 文本語境對齊），留待該 Task 驗證。

延遲量測：Task 3 Step 10 執行 eval 實跑時**未**用 `time` 記錄總耗時，因此沒有可直接比較的既有基準數字。為求嚴謹，改為在同一台機器、同一版程式碼下做 A/B：

| 情境 | 指令 | 總耗時（`time`，wall clock） | 每題平均（÷38 題） |
| --- | --- | --- | --- |
| 舊行為（`RAG_VECTOR_MIN_SCORE=0.5`） | `RAG_VECTOR_MIN_SCORE=0.5 python scripts/rag_eval.py --rank-mode cohere --top-n 5` | 52.122s | ≈1372ms |
| 新行為（`RAG_VECTOR_MIN_SCORE=0.0`，即本 change 後預設） | `python scripts/rag_eval.py --rank-mode cohere --top-n 5` | 49.699s | ≈1308ms |

新行為反而快了約 64ms/題，在網路呼叫（Gemini embedding + Cohere rerank + MongoDB Atlas）normal jitter 範圍內，判定為雜訊而非退化，未超過 300ms 的風險門檻，因此不需要調降 `RAG_RETRIEVE_CANDIDATES`。

## Task 9 驗證結果：reranker 輸入補回標題後的 eval 對照

實跑指令：`python scripts/rag_eval.py --compare-rerank --top-n 5 --out /tmp/rag-c2.json`（2026-08-08，已套用本 change 目前全部變更：D1 移除 min_score + D2 reranker 文本補回標題）。

與 `rag-eval-metrics` change 記錄的 baseline、Task 8 的 cohere rank-mode 結果並列：

| 指標 | Baseline（cohere rank-mode，`rag-eval-metrics`） | Task 8（移除 min_score，cohere rank-mode） | Task 9 本次（title 補回，`--compare-rerank` 的 cohere 分支） |
| --- | --- | --- | --- |
| `hit_rate@5` | 0.412 | 0.4117647058823529 | 0.38235294117647056（13/34） |
| `mean_mrr` | 0.198 | 0.198 | 0.21666666666666665 |
| `mean_ndcg@5` | 0.253 | 0.253 | 0.2574747757900341 |

同一次 `--compare-rerank` 執行另有 vector 分支（同一批 wide-retrieve 候選，僅用向量分數排序、不呼叫 Cohere），與 cohere 分支並列：

| 指標 | vector（本次） | cohere（本次） | delta（cohere − vector） |
| --- | --- | --- | --- |
| `hit_rate@5` | 0.7058823529411765 | 0.38235294117647056 | -0.324 |
| `mean_mrr` | 0.35784313725490197 | 0.21666666666666665 | -0.141 |
| `mean_ndcg@5` | 0.4471086115066822 | 0.2574747757900341 | **-0.190** |

`fixed_by_cohere`（vector 沒中但 cohere 中）：`kb-019`, `kb-026`（2 題）。
`regressed_by_cohere`（vector 有中但 cohere 沒中）：`kb-002`, `kb-004`, `kb-005`, `kb-006`, `kb-009`, `kb-012`, `kb-013`, `kb-014`, `kb-021`, `kb-024`, `kb-025`, `kb-029`, `kb-033`（13 題）。無 `error_ids`（兩分支皆為空），排除 API 呼叫失敗干擾結果的可能。

**與 Task 3 早先觀察的對照**：Task 3（本 change 任何程式碼變更之前）跑同一指令得到 `hit_rate_delta: -0.294`、`ndcg@5_delta: -0.194`（見 `task-3-report.md`）。本次（D1+D2 都已套用）為 `hit_rate_delta: -0.324`、`ndcg@5_delta: -0.190`。兩次數字幾乎一致，差距落在雜訊範圍內（線上 Gemini embedding／Cohere API／MongoDB Atlas 皆非完全確定性），**cohere 劣於 vector 的方向完全沒有被扭轉**。

**誠實結論：本 Task 的主要假說未被證實。** 若「reranker 因看不到標題語境而排序較差」這個假說成立，補回標題後 cohere 分支應明顯逼近甚至超過 vector 分支；但實測中 cohere 依然大幅劣於 vector（`ndcg@5_delta = -0.190`，幾乎與補標題前的 `-0.194` 相同），且 cohere 單獨對 baseline 相比也只有雜訊等級的變化（`mean_ndcg@5`: 0.253 → 0.257，+0.004；`hit_rate@5`: 0.412 → 0.382，反而略降，但只差 1 題，34 題中 1 題差異落在單次線上呼叫抽樣雜訊範圍內）。

已用單元測試（`test_cohere_reranker_sends_title_prefixed_documents`）確認程式碼確實把 `f"主題：{title}\n內容：{content}"` 送進 Cohere API payload，排除「程式碼沒生效」的可能。這代表 Cohere reranker 排序品質差的根因**不是**（或至少不只是）標題缺失於送出文本——`fixed_by_cohere` 僅 2 題、`regressed_by_cohere` 高達 13 題的懸殊比例指向更根本的原因（例如 Cohere rerank 模型本身在此中文醫療領域語料上的判斷力、wide-retrieve 候選集組成、或 golden set 標準答案的 URL 匹配方式），非本 change 範圍。D2 的程式碼變更本身是正確且必要的（讓三階段語境收斂本身是合理的工程修正），但**未觀測到預期的排序品質提升**，忠實記錄此負面結果，不因此調整任何程式碼或數字。
