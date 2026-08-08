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

絕對相似度門檻只在「分數是 cosine 相似度」的前提下才有意義；`0.5` 是針對 cosine 訂的經驗值。但在 hybrid 檢索路徑下，`app/services/rag/retriever.py` 的 RRF 融合會把 `metadata["score"]` 覆寫為融合後的分數（見 `retriever.py:218` 附近註解：BM25 分數沒有上界、也依語料庫統計而變，刻意不做 min_score 過濾），此時再對它套用 0.5 門檻在語意上是錯的——融合分數的尺度與 cosine 相似度完全不同。

因此不是把 0.5 調低成另一個猜測值，而是移除硬門檻本身：在 wide retrieve → rerank 架構下，第一階段的職責是最大化召回，過濾與排序交給精排階段負責。設定項 `RAG_VECTOR_MIN_SCORE` 保留（預設 `0.0`），使未來如果真的需要在向量階段做門檻過濾，仍可由環境變數調回非零值，而不必改程式碼。

### D2. reranker 文本格式對齊 embedding

送入 Cohere Rerank 的 document 文本刻意與上游 `main_pipeline.get_embedding` 的 `f"主題：{title}\n內容：{chunk}"` 格式完全一致（`主題：{original_title}\n內容：{chunk}`），使向量檢索、BM25（hybrid）、精排三個階段看到的語境盡量收斂到同一份文本。`original_title` 缺失時（例如來源資料本身沒有標題）退回純內容，不阻斷精排流程。

精排函式回傳的 `Document.page_content` 維持原始 `chunk_content`（不含標題前綴）不變——組出的「主題：…\n內容：…」文本只用於送給 Rerank API 判斷相關性，不影響下游 `_build_context` 組 prompt 或 `_append_sources` 顯示來源時使用的內容。

### D3. 為何不順手改 BM25 索引欄位補標題

在 `chunk_content` 補標題（即讓 BM25 索引也吃到 `主題：{title}\n內容：{chunk}` 格式）需要重寫全部 4,605 筆既有文件的內容欄位，這是資料層級的批次遷移，且 `chunk_content` 是外部 ETL repo（`Capoo0618/CARE-data`）每日寫入的欄位，屬上游職責範圍（詳見 `docs/care-data-issues.md`）。本 change 只在應用層（reranker 送出的請求文本）做語境補償，不改動資料庫既有欄位內容，範圍與風險都可控；資料層級的標題回填留待上游 ETL 或另一個 change 處理。
