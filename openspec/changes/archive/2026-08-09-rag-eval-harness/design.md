## Context

CARE 已有：
- `scripts/rag_query_cli.py`：單題向量檢索除錯
- `RagAnswerService`：檢索 →（未來 rerank）→ 生成 → 附來源
- 無 golden set、無批次評測、無回歸閘門

文章建議：從 production 抽樣建 eval set；先能量測再談 CRAG。本設計採**最小可用**，避免一開始上 RAGAS／LLM-as-judge 全家桶。

## Goals / Non-Goals

**Goals:**

- 一份可版本控管的 JSONL 題庫（目標 30–50；先 schema + ≥5 範例）
- 一支腳本：讀題庫 → 跑 pipeline → 輸出 JSON／markdown 摘要
- 粗指標可區分「rerank on/off、top_n、model」等設定差異
- 標註成本低：每題約 1–2 分鐘

**Non-Goals:**

- 不上 RAGAS／DeepEval／Langfuse（可後續）
- 不做 LLM-as-judge 自動打 faithfulness（第一版用規則／字串重疊；可選第二版再加）
- 不自動從 LINE webhook log 匯入（文件教手動抽樣即可）
- 不阻擋 merge 的強制 CI（除非之後有 fixture 模式且穩定）

## Decisions

### D1. 題庫格式：JSONL，一題一行

```json
{
  "id": "kb-001",
  "query": "高血壓平常要注意什麼？",
  "route": "kb",
  "expected_url_substrings": ["health.gov", "hpa.gov"],
  "must_not_answer": false,
  "notes": "應引用衛教文章"
}
```

| 欄位 | 必填 | 說明 |
|------|------|------|
| `id` | ✓ | 穩定 id |
| `query` | ✓ | 使用者問句（貼近 LINE 語氣） |
| `route` | ✓ | `kb` \| `refuse` \| `web`：期望主路徑 |
| `expected_url_substrings` | kb 建議有 | 期望來源 URL 需包含的子字串（比完整 URL 好標） |
| `must_not_answer` | | `true` 表示應拒答／無資料提示（對應 refuse 或 KB 無命中） |
| `notes` | | 人工備註 |

`route` 語意：
- `kb`：應靠知識庫答得出，且來源至少命中一個 substring
- `web`：KB 可能弱，期望 agent／人工路徑走網路（第一版腳本可只評「若跑 full answer，是否出現網路前綴」；或先 skip full agent）
- `refuse`：非健康或應拒答 → 不應胡謅醫療建議

### D2. 評測範圍分兩層（可開關）

1. **retrieval-only（預設、便宜）**  
   只跑 retriever（+ 若已接 rerank 則含精排後 top_n）。  
   指標：`hit_at_k`（任一 `expected_url_substrings` 出現在回傳 docs 的 url）

2. **answer（可選 `--with-answer`）**  
   跑 `RagAnswerService.answer`。  
   指標：
   - `source_hit`：回答附的來源是否命中期望 substring
   - `refuse_ok`：`must_not_answer` 時是否命中無資料／無法回答類訊息
   - `nonempty`：答案非空
   - （粗）`hallucination_flag`：僅規則示警——例如 `must_not_answer` 卻給了長篇肯定句；**不做**嚴格事實核查

第一版**不跑完整 LangGraph agent**（避免 tool 路由噪音）；agent 路由另開 eval。

### D3. 報告輸出

- stdout：摘要（題數、hit@k 比例、失敗 id 列表）
- `--out report.json`：每題明細（回傳 urls、scores、答案摘要）
- exit code：可選 `--fail-under 0.6` 讓 hit 率低於門檻時非 0（給本機／nightly 用）

### D4. 資料目錄

```
evals/rag/
  README.md           # 怎麼抽題、怎麼標、怎麼跑
  golden.jsonl        # 主題庫（目標 30–50）
  golden.example.jsonl # 範例（可與 golden 合併，至少 5 題示範）
```

### D5. 抽題與標註流程（人工作業，寫進 README）

1. 從 LINE 真實對話／客服筆記／自己常測的問句收集 40 句左右  
2. 每題標 `route`  
3. 對 `kb` 題：先用 `python scripts/rag_query_cli.py "問句"` 看實際撈到的 url，把「正確該引用」的網域／路徑片段寫進 `expected_url_substrings`  
4. 刻意加：拒答題、易混淆題、同義改寫題、無資料題  
5. **留 20% 當 holdout**：`split: train|holdout` 欄位可選；調參只看 train，發佈前看 holdout（文件說明即可，腳本可 filter）

### D6. 與 rerank change 的關係

- 可平行開發；rerank 合併後，同一題庫跑兩次（`COHERE_API_KEY` on/off 或 `RAG_RERANK_TOP_N=5 vs 8`）比 hit@k  
- eval harness **不依賴** rerank 一定存在：有就測精排後結果，沒有就測向量 top

## Risks / Trade-offs

- [題庫與真實分布脫節] → README 要求從真實問句抽，禁止全是「教科書完美問法」
- [expected_url 標錯] → 用 substring 降標註難度；允許一題多個可接受來源
- [答案層無自動 faithfulness] → 第一版接受；命中率先解決「有沒有撈對」
- [打真實 DB 的費用／環境] → 文件標明需 `.env`；CI 預設不跑，或僅跑解析 JSONL 的單元測試

## Migration Plan

1. 合併 harness + 範例題
2. 團隊花 1～2 小時補到 ≥30 題
3. 建立「改 RAG 前必跑」慣例：`python scripts/rag_eval.py --out /tmp/rag-report.json`
4. rerank PR 附上 before/after hit@k

## Open Questions

1. 題庫是否含個資？→ **禁止**寫入真實姓名／病歷；問句需去識別化  
2. `web` 題第一版要不要自動評？→ 建議先標註保留，腳本對 `web` 預設 skip 或只記 retrieval miss
