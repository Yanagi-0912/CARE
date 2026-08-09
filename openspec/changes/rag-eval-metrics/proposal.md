## Why

CARE 的 RAG 知識庫問答目前 top-5 hit_rate 偏低（實測 vector 0.29 / cohere 0.44），但 eval 只有 binary hit_rate 一種指標，命中就是 1、沒命中就是 0，rerank 前後排序品質的差異看不出解析度——同樣命中一筆，排第 1 名還是第 5 名對使用者體驗差很多，現行指標無法反映。

更嚴重的是生成端：`_build_context` 只把命中的 chunk 純文字拼接送進 prompt，不含來源資訊；prompt 也未要求模型逐句標註引用編號。「參考資料來源」清單是生成完答案後，另外依檢索分數排序貼上去的，跟答案正文實際引用了哪些內容毫無關聯——使用者可能讀到一段完全沒提到來源 A 的敘述，底下卻附了來源 A 的連結。此外，食藥署那批 1,367 chunks（佔知識庫 30%）`url` 全為 `None`，`_append_sources` 的 `if not url: continue` 會把整批直接跳過，即使檢索命中也無法被列為參考來源。

本 change 先補齊「量得出差異」與「來源對得上答案」這兩個基礎，作為後續 `rag-retrieval-tuning` change 調整檢索與 rerank 參數時的可信量測基準。

## What Changes

- `eval_scoring` 新增 `expected_title_substrings` 標籤（比對 `original_title`），作為不隨切片方式改變的穩定判準
- 新增 `MRR`、`nDCG@5` 指標；**不做 recall@k**（golden set 缺 exhaustive relevance judgments，分母不存在）
- 兩個 retriever（`MongoAtlasVectorRetriever`、`MongoAtlasTextRetriever`）的 `$project` 加入 `original_title`
- context 改為帶編號與出處標頭（`來源`／`標題`，不含 url）；prompt 要求逐句標 `[n]`
- 「參考資料來源」改為只列**實際被引用**者，依首次引用順序連續重編號
- 無 `url` 的來源以「來源名｜標題」呈現，不再被 `if not url: continue` 靜默丟棄
- 模型未輸出任何 `[n]` 時不附來源，記錄 `citation_missing` log
- 新增 citation coverage 指標，量測「命中卻無法被引用」的發生率
- **非 BREAKING**：對外 tool（`get_rag_answer`）介面不變，僅改變回覆內文的來源呈現方式與內部 eval 指標

## Capabilities

### New Capabilities

- （無）

### Modified Capabilities

- `rag-responses`：「檢索上下文與參考來源上限」由「SHALL 只列出最多 3 筆關聯度最高的網址」改為「SHALL 只列出實際被引用的來源，最多 3 筆，依首次引用順序連續編號；當來源缺少 `url` 時，SHALL 以 `來源名｜標題` 呈現，不得靜默丟棄」。

## Impact

- **程式**：`app/services/rag/retriever.py`、`app/services/rag/eval_scoring.py`、`app/services/rag/answer_service.py`、`app/services/rag/answer_prompts.py`、`scripts/rag_eval.py`
- **文件**：`evals/rag/README.md`（題庫欄位表、nDCG 口徑註明）
- **API／route**：無新 HTTP route；僅影響 `get_rag_answer` tool 內部回覆內文與 eval 腳本輸出
- **測試**：`tests/unit/services/rag/` 新增／更新（`test_retriever.py`、`test_eval_scoring.py`、`test_answer_service.py`、`test_answer_prompts.py`）；`./init.sh`（或 pytest）全綠才算完成
- **相依**：無新增套件
