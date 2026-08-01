## Context

CARE RAG 現況：

```
query → Gemini embed → Atlas $vectorSearch (limit=k=10, min_score=0.5)
     → 全部進 prompt → Gemini 生成 → 附最多 3 筆來源
```

痛點：bi-encoder 只做召回，沒有 query–document 交互精排；對的 chunk 可能落在 top-10 外或排在後面卻進了 noisy context。

約束：

- 維持 `app/services/rag/` + `dependencies.py` 組裝；不引入 LlamaIndex
- LINE 延遲敏感：rerank 預算約 +50–200ms
- 健康語料多為繁中／中英混雜 → 需支援 multilingual 的 Cohere 模型
- 無 API key 的本機／CI 環境仍須可跑測試與基本 RAG

## Goals / Non-Goals

**Goals:**

- 兩階段檢索：wide retrieve → Cohere Rerank → top-n 進生成
- 供應商固定為 **Cohere**（API：Rerank）
- 失敗可降級、行為可測、設定可用 env 調整
- 更新 `rag-responses` 對 context 筆數的契約

**Non-Goals:**

- 不在此變更做 CRAG／HyDE／query rewrite／hybrid BM25
- 不重灌／重解析知識庫 PDF
- 不更換 embedding 模型或 Atlas index
- 不做正式 golden-set eval harness（可另開 change；本變更只要求單元測試）

## Decisions

### D1. 供應商與模型：Cohere Rerank API

- **選擇**：`COHERE_API_KEY` + 模型預設 **`rerank-v4.0-pro`**（Rerank 4.0，多語，含中文；官方文件範例亦用此 id）
- **理由**：使用者偏好 Cohere；截至官方 models／rerank docs，最新代為 Rerank 4.0（`rerank-v4.0-pro` / `rerank-v4.0-fast`），`rerank-v3.5` 仍可用但已非最新
- **同系列替代**：
  - `rerank-v4.0-fast`：同代輕量版，延遲／成本較低、品質通常略遜於 pro（可用 env 切換）
  - `rerank-v3.5`：上一代多語，僅在需相容舊配額／定價時考慮
  - Voyage／本機 MiniLM：非本次偏好
- **呼叫方式**：實作採既有 `httpx` 薄 HTTP client（`POST https://api.cohere.com/v2/rerank`），不新增 `cohere` SDK；介面維持 `Reranker` protocol，`http_post` 可注入以便單元測試

### D2. Pipeline 插入點：`RagAnswerService` 內、生成前

```
docs_wide = await retriever.ainvoke(query)          # limit = RAG_RETRIEVE_CANDIDATES (default 40)
docs_ranked = await reranker.rerank(query, docs_wide, top_n=RAG_RERANK_TOP_N)  # default 5
answer = await generate(query, docs_ranked)
sources from docs_ranked[:CITE_TOP_K]
```

- **理由**：retriever 維持「向量召回」單一職責；rerank 屬 post-retrieve，放在 answer service 最清楚
- **替代方案**：包進 retriever → 不利單獨 mock／替換；當 NodePostprocessor 框架 → 與現有非 LlamaIndex 架構不合

### D3. Retriever 參數調整

| 參數 | 現況 | 變更後預設 | Env |
|------|------|------------|-----|
| 向量 `limit` / `k` | 10 | **40**（candidate） | `RAG_RETRIEVE_CANDIDATES` |
| `numCandidates` | `k * 30` | 維持公式（candidate×30） | — |
| `min_score` | 0.5 | **維持 0.5**（先過濾明顯無關） | 既有／可另加 env |
| rerank `top_n` | — | **5** | `RAG_RERANK_TOP_N` |
| 引用上限 | 3 | **3**（不變） | — |

- Wide retrieve 後仍套用 `min_score`，避免把明顯低分垃圾丟給 Cohere（省成本與雜訊）
- 若過濾後不足 `top_n`，以實際筆數精排／進 prompt

### D4. 降級策略

| 情況 | 行為 |
|------|------|
| 未設定 `COHERE_API_KEY` | 跳過 rerank；對 wide 結果依向量 `score` 取前 `top_n`；log warning 一次級別 |
| Cohere timeout／5xx／SDK 例外 | 同上降級；該請求 log warning（含錯誤類型，不含文件全文） |
| candidates 空 | 維持現有 `NO_HITS_MESSAGE`，不呼叫 Cohere |

### D5. Metadata

- Rerank 後在 `Document.metadata` 寫入 `rerank_score`（若 API 有回傳）與 `rerank_rank`（1-based）
- 保留原 `score`（向量分）供除錯
- 來源排序：依 rerank 後順序，而非向量分

### D6. 組裝與設定

- `Settings` 新增：`COHERE_API_KEY`、`COHERE_RERANK_MODEL`（default `rerank-v4.0-pro`）、`RAG_RETRIEVE_CANDIDATES`、`RAG_RERANK_TOP_N`、`COHERE_RERANK_TIMEOUT_SECONDS`（default `5`）
- `dependencies.py` 組裝 `CohereReranker`（或 `NoOp`／null object 在無 key 時）注入 `RagAnswerService`
- `.env.example` 與部署 secret 文件同步列出 `COHERE_API_KEY`
- `CARE-infra` cicd secret 注入另開追蹤或同 PR 附帶（見 Migration）

### D7. 測試策略

- Unit：mock Cohere client
  - 成功時順序被重排、只留 top_n
  - 失敗時降級到向量排序
  - 無 key 不發 HTTP
  - 空 candidates 不呼叫
- 不強制 CI 打真實 Cohere（避免金鑰與費用）

## Risks / Trade-offs

- [額外延遲／費用] → 限制 candidates≤50、top_n≤5、timeout 5s；失敗快速降級
- [中文醫療術語品質] → 預設 `rerank-v4.0-pro`；若延遲／費用吃緊可改 `rerank-v4.0-fast`，並用 eval 驗證
- [祕密未注入導致「以為有精排其實沒有」] → 啟動或首次 RAG 時 warning；可選 metrics／log 欄位 `rerank=skipped|ok|error`
- [min_score 過高砍掉可救 chunk] → 先維持 0.5；若 eval 顯示召回不足再調低 candidate 階段門檻（另議）
- [進 prompt 從 10 降到 5] → 刻意換 precision；若長文摘要題變差可把 `RAG_RERANK_TOP_N` 調到 8

## Migration Plan

1. 合併程式至 `main`（預設無 key 時行為≈「向量 top-n」，比舊版少塞 context 但可接受；若需完全兼容可暫時把 candidates=top_n=10 且無 key 時取 10）
2. 於本機／staging 設定 `COHERE_API_KEY` 驗證延遲與答案品質
3. 更新 `CARE-infra` secret：`kubectl`/CI `care-backend-secret` 增加 `COHERE_API_KEY`
4. 滾動部署後觀察 log：`rerank=ok` 比例與 5xx
5. Rollback：清空／移除 `COHERE_API_KEY` 即回降級路徑；或 revert 部署

**建議過渡預設**：無 key 時 wide 後取 `min(top_n, len)`；上線有 key 後自然啟用精排。

## Open Questions

1. `CARE-infra` 的 `COHERE_API_KEY` 是否在本 change 的實作 PR 一併改 cicd，或分開 PR？（建議一併，否則 prod 永遠降級）
2. 是否需要對「參考資料來源」同時顯示向量分／rerank 分？（建議否，僅內部 log）
3. Cohere trial／prod 配額是否足夠 LINE 尖峰？（上線前用預估 QPS × 每次 1 次 rerank 估算）
