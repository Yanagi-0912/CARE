# RAG 檢索品質改善設計（2026-08）

> 狀態：已確認，待實作
> 起點：`evals/rag/README.md` 記錄本機 top-5 hit_rate = vector 0.29 / cohere 0.44

## 1. 問題

知識庫問答的 top-5 hit_rate 只有 0.29。2026 的共識是 RAG 失敗點幾乎都在檢索而非生成，
而 0.29 這個量級不是 rerank 調參救得回來的 —— rerank 只能重排已撈到的候選。
Cohere 能把 0.29 拉到 0.44，代表排序階段是健康的，壞的是第一階段召回與量測本身。

## 2. 實測發現

以下皆為對線上 `health_articles_chunks` 與 Gemini API 的實測結果，非推論。

### 2.1 知識庫組成

| 來源 | chunks | 佔比 | 取得方式 | url |
| --- | --- | --- | --- | --- |
| 衛福部闢謠網站 | 2,840 | 62% | JSON API | 有 |
| 食藥署闢謠專區 | 1,367 | 30% | JSON API | **全為 None** |
| 台灣事實查核中心 | 132 | 2.9% | BeautifulSoup | 有 |
| CARE `IngestService` | 266 | 5.8% | Firecrawl | 有 |

總計 4,605 chunks / 942 url。94% 的資料由外部 ETL repo
[`Capoo0618/CARE-data`](https://github.com/Capoo0618/CARE-data) 每日 08:00 寫入，
**不受本 repo 控制**。

### 2.2 食藥署 API 沒有文章網址（影響 30% 的 KB）

食藥署 `DataAction` API 欄位僅 `['標題', '內容', '附檔連結', '發布日期']`，
不提供文章網址（`附檔連結` 是附件，實測值為字串 `'None'`）。

**確定的後果 —— 無法被引用**：`answer_service._append_sources` 的
`if not url: continue` 會跳過整批。食藥署內容即使完美命中，使用者也看不到來源；
`eval_scoring.is_source_hit` 從答案的來源區塊抽 url 比對，同樣驗不到這批。
citation coverage 因此存在結構性上限。

**檢索計分則未被完全阻斷**（此處修正初判）：`is_doc_retrieval_hit` 除了 url
也接受 `expected_content_substrings`。實測 golden 38 題中 kb 佔 34 題，
全部具備 content substring（29 題另有 url，5 題僅有 content），因此都能計分。

但現行 content substring 是 `scripts/rag_tighten_golden.py` 自動截取的
約 25 字片段，例如 `"用。至於糖尿病患者，劉皓軒說，營養師常常跟患"` —— 起點落在句中。
這種標籤有兩個弱點：

1. 脆弱：**切片方式一改，全部失效**。若日後推動 ETL 修復（交付 A），
   所有 content 標籤都要重做。
2. 無法涵蓋 citation 層的驗證。

因此仍需引入 `expected_title_substrings`（比對 `original_title`）作為**穩定標籤** ——
標題不隨切片方式改變。

### 2.3 向量庫編在 query 空間

`CARE-data/main_pipeline.py` 呼叫 `embedContent` 時未指定 `taskType`。實測預設值：

```
cos(未指定, RETRIEVAL_QUERY)    = 1.000000
cos(未指定, RETRIEVAL_DOCUMENT) = 0.927816
```

未指定即等同 `RETRIEVAL_QUERY`。全庫 4,605 筆文件都編在查詢空間，
而 CARE 查詢端亦用 `RETRIEVAL_QUERY`，形成 query-query 對比，
偏離 `gemini-embedding-001` 的非對稱檢索設計。

**限制**：本次僅證實「預設等同 QUERY」。實際傷害多少排序品質，
單一 cosine 比較無法證明（不同空間絕對值不可比），須以 `rag_eval.py` A/B 驗證。

### 2.4 標題只進 embedding，rerank 看不到

ETL 以 `get_embedding(f"主題：{title}\n內容：{chunk}")` 產生向量，
但寫入 Mongo 的 `chunk_content` 不含標題。

| 階段 | 讀到的文本 | 有標題？ |
| --- | --- | --- |
| 向量檢索 | embedding（含標題） | 是 |
| BM25 | `chunk_content` | 否 |
| Cohere rerank | `chunk_content` | 否 |

reranker 收到的是缺語境的斷句碎片。這是 rerank 增益受限的合理解釋，
且 **可在 CARE 端單獨修復**，不需改 ETL、不需重建知識庫。

### 2.5 切片與清洗

- ETL 以 `chunk_text(text, chunk_size=500, overlap=50)` 固定字元硬切，不辨句界。
  實測：`'元整及55萬8,000元。國民健康署呼籲...'` 開頭即為半句。
- 尾段殘渣：127 筆長度 1 字元（`'3'`、`'。'`、`'×'`），480 筆 < 100 字元（10.4%）。
- `utils.clean_html` 是 `re.sub(r'<[^>]+>', '', ...)` 正則去標籤，非 BeautifulSoup
  （與該 repo README 描述不符）；`<script>`/`<style>` 的內容會被留為正文。
- `re.sub(r'\s+', ' ', ...)` 將換行壓為空格，段落結構全滅 —— 這是資料中不存在
  `\n\n` 的原因，也是只能硬切的根源。

### 2.6 CARE 端的 Firecrawl ingest 品質

`IngestService` 產出的 266 筆抓的是整頁導覽列。`https://www.fda.gov.tw/` 首頁
切出 49 個 chunk，內容為 `'一站式搜尋'`、`'## 主視覺與專區連結'`、
`'[跳到主要內容區塊]'`。對醫療問答為純噪音。

### 2.7 已確認正常，不需處理

- Hybrid 檢索已上線：`care_text_index` 狀態 READY，analyzer 為 `lucene.cjk`，
  `$search` 實測有結果，`.env` 的 `RAG_HYBRID_ENABLED=true`。
- `rerank-v4.0-pro` 為當前第二強 reranker，不需更換。
- 查詢端 `task_type` 已正確區分 `RETRIEVAL_QUERY` / `RETRIEVAL_DOCUMENT`。

## 3. 為什麼不改用 Firecrawl 爬取

91% 的資料來自兩支回傳結構化 JSON 的政府 API，Firecrawl 對 API 無用武之地。
且本專案已有 Firecrawl 的實測結果 —— 即 2.6 節那 266 筆導覽列噪音。
真正需要網頁爬取的僅 TFC 的 132 chunks（2.9%），現有 `find_all('p')` 堪用。
為 2.9% 的資料引入每日執行的付費 API，成本效益不成立。

**問題在清洗與切片，不在抓取。**

## 4. 範圍

### 4.1 交付 A — CARE-data 問題報告

`docs/care-data-issues.md`。非 openspec change，是交給 ETL repo 維護者的文件。
每項含：現象 → 實測數據 → 根因（指到行號）→ 建議修改。
涵蓋 2.2、2.3、2.4、2.5 全部項目，加上 early-stopping 漏抓、
只 insert 不 update（`發布日期`/`修改日期` 欄位被丟棄）、`verify=False` 關閉 TLS 驗證。

### 4.2 交付 B — openspec change `rag-eval-metrics`

| 項 | 內容 | 檔案 |
| --- | --- | --- |
| B1 | golden 支援 `expected_title_substrings`，比對 `original_title`，提供不隨切片方式失效的穩定標籤 | `app/services/rag/eval_scoring.py`、`evals/rag/README.md` |
| B2 | 加 nDCG@5、MRR（不做 recall@k，理由見下） | `eval_scoring.py`、`scripts/rag_eval.py` |
| B3 | 修生成端引用 | `retriever.py`、`answer_service.py`、`answer_prompts.py` |
| B4 | citation coverage 指標（依賴 B3） | `eval_scoring.py`、`scripts/rag_eval.py` |

B2 為何不做 recall@k：recall@k 需要「該題在整個語料庫中共有幾筆相關文件」
這個分母，而 golden set 只標了每題一個正解來源，沒有窮盡的相關性判準
（exhaustive relevance judgments）。硬算出來的分母是假的，指標會失去意義。
可算且有意義的是：

- `hit_rate@k`：top-k 是否至少命中一筆（沿用既有定義，供新舊口徑對照）
- `MRR`：第一筆命中的排名倒數，直接反映「有沒有排上去」
- `nDCG@5`：二元 gain 的位置加權，rerank 前後差異的主要觀測指標

三者以既有的 substring 判準決定單篇 relevance（url／title／source／content
任一命中即 gain=1）。

B3 細節：

- `MongoAtlasVectorRetriever` / `MongoAtlasTextRetriever` 的 `$project` 加入 `original_title`
- context 每筆改為帶編號與出處標頭的固定格式，讓模型知道自己在引用誰：

  ```
  [1] 來源：衛福部闢謠網站｜標題：捍「胃」健康 過年聚餐用公筷
  幽門螺旋桿菌是導致胃癌的主要風險因子……
  ```

  標頭僅含 `來源` 與 `標題`，**不放 url**（url 進 context 會消耗 token
  且模型可能改寫或杜撰網址；url 由 `_append_sources` 依編號對應回填）
- prompt 要求逐句標 `[n]`，`n` 對應上述 context 編號
- `_append_sources` 只列出**實際被引用**的來源，並從 1 連續重編號
- 無 URL 的來源以 `來源名｜標題` 呈現，不再被靜默丟棄
- 模型未輸出任何 `[n]` 時不附來源，並記錄 `citation_missing` log；
  此行為的發生率由 B4 的 citation coverage 直接量測，作為安全網
- 受 `openspec/specs/line-reply-rules` 約束：純文字、不得輸出 Markdown、最多 3 筆

**這是 `rag-responses` 的 MODIFIED capability**：現行 spec 規定
「SHALL 只列出最多 3 筆關聯度最高的網址」，改為「只列出實際被引用的來源，
最多 3 筆，依首次引用順序連續編號」。openspec change 的 proposal 須明確宣告此變更。

### 4.3 交付 C — openspec change `rag-retrieval-tuning`

| 項 | 內容 | 檔案 |
| --- | --- | --- |
| C1 | 移除 `min_score` 預設 0.5（改 0.0，參數保留），過濾交給 reranker | `retriever.py`、`config.py` |
| C2 | rerank 輸入補回標題，格式對齊 embedding 時的 `主題：{title}\n內容：{chunk}` | `cohere_reranker.py`、`answer_service.py` |
| C3 | 清除 266 筆 Firecrawl 導覽列噪音，腳本須支援 dry-run | `scripts/` 新增 |
| C4 | 修正 Atlas Search index 範本欄位名 `text` → `chunk_content` | `resources/atlas_text_search_index.json` |

C1 理由：在 wide retrieve → rerank 架構下，第一階段職責是衝 recall，
過濾是 reranker 的工作。0.5 是針對 cosine 的絕對門檻，
在候選進 reranker 前就先砍掉一批，與架構意圖相反。

C2 理由：見 2.4。這是不需重建知識庫就能取得的增益。

### 4.4 非範圍

- 不改 CARE-data 程式（僅交付報告）
- 不重建知識庫、不重跑 embedding
- 不更換 embedding 模型或向量維度
- 不引入 agentic RAG（成本 3–10x，且目前瓶頸在召回不在推理深度）
- 不改用 MongoDB 原生 `$rankFusion`（現有 Python RRF 運作正常）
- 不更換 reranker 供應商

## 5. 執行順序與驗證

```
B1 ──► 加穩定標籤（title），重跑 rag_eval.py 取得 baseline
 │
B2 ──► 加 nDCG@5 / recall@k / MRR，解析度足以分辨排序差異
 │
 ▼
B3 ──► 修引用（context 帶標頭、逐句標 [n]、只列實際引用）
 │
 ▼
B4 ──► citation coverage 上線，此時才有非零意義
 │
 ▼
C1 ──► 移除 min_score，跑 eval 比對
 │
 ▼
C2 ──► rerank 輸入補標題，跑 eval 比對    ◄── 預期最大單筆增益
 │
 ▼
C3, C4 ► 清噪音、修範本
```

每一步後執行 `python scripts/rag_eval.py --rank-mode cohere --top-n 5` 並記錄數字。

**口徑注意**：0.29 / 0.44 是舊 hit_rate（binary、含 content substring）。
B1 加入 title 標籤後計分口徑改變，數字不可與之直接相比；
B2 的 nDCG@5 更是全新指標。報告須同時輸出新舊兩種 hit_rate 以便對照。

## 6. 測試策略

- 依 `openspec/config.yaml` 規定：**禁止 monkey patch**，一律以依賴注入傳入 mock
- 單元測試置於 `tests/unit/services/rag/`，對應既有檔案命名
- 新增指標（nDCG/recall/MRR/citation coverage）須有純函式層級測試，
  用固定的 ranked list 與 golden 驗證數值，不呼叫外部服務
- `_append_sources` 的重編號與無 URL fallback 須有測試涵蓋
- reranker 輸入格式變更須測「有 title」與「無 title」兩種 document
- Definition of Done：`./init.sh` 全綠

## 7. 風險

| 風險 | 處置 |
| --- | --- |
| 移除 `min_score` 後低分噪音進 reranker，延遲上升 | 候選數已固定為 40，reranker 負載不變；量測 p95 延遲 |
| 逐句標 `[n]` 可能讓 LINE 回覆變冗長 | 受 line-reply-rules 約束，prompt 明確限制格式；人工抽驗 |
| C3 刪除腳本誤刪正常資料 | 強制 dry-run 預設、以 `content_hash` 存在與否為條件、先輸出待刪清單 |
| B1 改變計分口徑後數字不可與 0.29 直接比較 | 報告中明確標注口徑變更，同時輸出新舊兩種計分 |
| ETL 每日 08:00 持續寫入舊格式資料 | 本次不處理；由交付 A 的報告推動上游修復 |
