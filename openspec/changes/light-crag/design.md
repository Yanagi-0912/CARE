## Context

現行 RAG（含 Cohere rerank）：

```
query → retrieve(~40) → rerank(top 5) → generate → 附來源
         └ 空命中 → NO_HITS_MESSAGE
```

Agent 另有 `search_public_web`，但 **RagAnswerService 不會**在內部觸發網路（`rag-responses` 契約）。痛點是：有撈到文件但內容其實答不了時，仍會進 generate，浪費延遲或產出低品質回答。

約束：

- 維持 DI／可測；grader／rewriter 以建構參數注入，禁止 monkey patch 全域
- LINE 延遲敏感：grader + 最多一次 rewrite，預算需可控
- 不破壞「RAG 不自動打網」契約；不足時回傳訊號，由 Agent 決定是否 web
- 已有 eval harness，可用來對照 on/off

## Goals / Non-Goals

**Goals:**

- Rerank 後、生成前做充足性分級：`correct` / `ambiguous` / `incorrect`
- `incorrect`（或空文件）：不生成，回傳既有無資料類訊息
- `ambiguous`：最多一次 query rewrite → 再跑 retrieve→rerank→grade；仍非 `correct` 則不生成
- `correct`：維持現有生成與來源附註
- Grader／rewrite LLM 失敗：降級為直接生成（等同關閉 CRAG）

**Non-Goals:**

- 不在 `RagAnswerService` 內呼叫 Firecrawl／web tool
- 不做完整 CRAG 論文級 corrective 迴圈、不做多輪 rewrite
- 不改 Agent graph 拓樸（第一版靠 tool 回傳文案讓模型改呼叫 web；必要時再另開 change 強化 tool description）
- 不上 HyDE／BM25 hybrid／換 LlamaIndex

## Decisions

### D1. 插入點：`RagAnswerService.answer` 內、rerank 之後

```
docs = retrieve(q)
ranked = rerank(q, docs, top_n)
grade = grader.grade(q, ranked)   # correct | ambiguous | incorrect
if incorrect → NO_HITS_MESSAGE（或不附來源的無資料訊息）
if ambiguous → q2 = rewrite(q, ranked); 再跑一輪；仍非 correct → 無資料
if correct → generate + sources
```

- **理由**：與 rerank 同層、單一職責清楚；tool／Agent 介面不變
- **替代**：獨立 LangGraph 節點 → 改動面大；放在 agent 外層 → 重複檢索邏輯

### D2. Grader：Gemini 結構化三態 enum（非只 boolean）

- 輸入：使用者問題 + 精排後 chunk 摘要（截斷，避免超長）
- 輸出：`correct`｜`ambiguous`｜`incorrect`（structured output）
- 實作：`RetrievalGrader` protocol + `GeminiRetrievalGrader`；測試注入 FakeGrader
- **替代**：只用 Cohere relevance 閾值 → 省一次 LLM，但無法表達「有關但不足以回答」；可當日後優化

### D3. 不足時的對外行為：對齊既有無資料訊息

- 使用既有 `NO_HITS_MESSAGE` 或同等「請換描述／目前無法提供」文案，**不**發明新的使用者可見協定字串（避免 LINE 體驗碎裂）
- 內部 log 標明 `crag_grade=incorrect|ambiguous_exhausted`
- Tool description 可微調：提到若回傳無資料可改試 `search_public_web`（非硬性 routing）

### D4. Rewrite：最多一次，便宜短 prompt

- `QueryRewriter`：依問題 +「為何不足」簡短指示，產出單一改寫問句
- 次數硬上限 1（env 不開放無限迴圈）
- 可選 `RAG_CRAG_ENABLED=true`（default true；CI／無 key 環境可關）

### D5. 與 eval 的關係

- 單元測試覆蓋分級分支與降級
- 可選：文件說明用同一 golden 跑「CRAG on/off」對 refuse／亂答的影響；不強制本 change 擴充 CLI

## Risks / Trade-offs

- [誤判 incorrect → 該答卻無資料] → Mitigation：預設偏 `ambiguous`／`correct` 的 prompt；失敗降級直接生成；用 eval 抽樣調 prompt
- [多一次／兩次 Gemini 延遲] → Mitigation：摘要截斷、temperature=0、可 env 關閉
- [Agent 未改呼叫 web] → Mitigation：微調 tool docstring；後續可另開 agent routing change
- [與「RAG 不自動上網」契約衝突疑慮] → Mitigation：明確不呼叫 web，只回無資料訊號

## Migration Plan

1. 實作 grader／rewriter + 接線 + 測試全綠  
2. 預設開啟；必要時 `RAG_CRAG_ENABLED=false` 瞬間回滾行為  
3. 部署後觀察 log 中 `crag_grade` 分布與使用者無資料率

## Open Questions

- 無（第一版採三態 + 一次 rewrite + 不內嵌 web；若上線後 Agent 很少改走 web，再開 agent 強化）
