## 1. 標題標籤與穩定判準

- [x] 1.1 `MongoAtlasVectorRetriever`、`MongoAtlasTextRetriever` 的 `$project` 加入 `original_title`，並放進回傳 `Document` 的 `metadata`
- [x] 1.2 `eval_scoring` 新增 `expected_title_substrings` 標籤解析、`titles_from_docs`（自 `Document.metadata` 取回標題清單）、`CaseResult.retrieved_titles`
- [x] 1.3 重構 `load_golden_jsonl` 中重複的 list 欄位解析邏輯（`expected_url_substrings`／`expected_content_substrings`／`expected_title_substrings` 共用同一段解析）
- [x] 1.4 更新 `evals/rag/README.md` 題庫格式表，補上 `expected_title_substrings` 欄位說明
- [x] 1.5 測試：`tests/unit/services/rag/test_retriever.py`、`tests/unit/services/rag/test_eval_scoring.py`

## 2. MRR 與 nDCG@5

- [x] 2.1 `eval_scoring` 新增 `doc_relevances`（依既有 substring 判準逐篇算 0/1 relevance）、`mrr`、`ndcg_at_k`（IDCG 以取回清單自身重排後計算，口徑見 `design.md` D2）
- [x] 2.2 `CaseResult` 新增 `.mrr`、`.ndcg_at_5`；`EvalSummary` 新增 `.mean_mrr`、`.mean_ndcg_at_5`
- [x] 2.3 `scripts/rag_eval.py` 印出 MRR、nDCG@5，並輸出 rerank 前後（vector vs cohere）的 nDCG delta
- [x] 2.4 `evals/rag/README.md` 註明 nDCG 的 IDCG 口徑（`design.md` D2），並標註新舊 hit_rate 計分口徑不可直接比較
- [x] 2.5 測試：`tests/unit/services/rag/test_eval_scoring.py`

## 3. context 編號與強制引用 prompt

- [x] 3.1 `RagAnswerService._build_context` 改為每筆文件帶編號與出處標頭（格式：`[n] 來源：{source_name}｜標題：{original_title}`，不含 url）
- [x] 3.2 `build_rag_prompt` 更新指示文字，要求模型逐句／逐項標註對應的 `[n]` 引用編號
- [x] 3.3 測試：`tests/unit/services/rag/test_answer_service.py`、`tests/unit/services/rag/test_answer_prompts.py`

## 4. 只列實際引用的來源

- [x] 4.1 於 `app/services/rag/answer_service.py` 新增 `cited_indices` 模組層級函式：自模型回覆文字中解析出實際出現過的 `[n]` 引用編號
- [x] 4.2 `_append_sources` 改為只列 `cited_indices` 命中的來源，依首次引用順序連續重編號（不再依檢索分數排序）
- [x] 4.3 無 `url` 的來源改以「來源名｜標題」呈現，移除 `if not url: continue` 的靜默丟棄
- [x] 4.4 模型回覆中未偵測到任何 `[n]` 時，不附加「參考資料來源」區塊，並記錄 `citation_missing` log（`design.md` D3）
- [x] 4.5 測試：`tests/unit/services/rag/test_answer_service.py`

## 5. Citation coverage 指標

- [x] 5.1 `eval_scoring` 新增 `answer_citation_count`（統計一則回答實際引用的來源筆數）
- [x] 5.2 `CaseResult` 新增 `.citation_count`；`EvalSummary` 新增 `.citation_coverage`（有引用的案例佔比）
- [x] 5.3 `scripts/rag_eval.py` 的 `--with-answer` 路徑填入 `citation_count`／`citation_coverage` 並輸出
- [x] 5.4 測試：`tests/unit/services/rag/test_eval_scoring.py`
- [x] 5.5 Definition of Done：`./init.sh` 全綠（所有 pytest 通過）
