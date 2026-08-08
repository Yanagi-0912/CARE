## Context

CARE RAG 現況：

```
query → 檢索（vector / hybrid）→ eval_scoring 算 binary hit_rate
     → _build_context 純文字拼接命中 chunk → Gemini 生成
     → _append_sources 依檢索分數另外貼上最多 3 筆來源（url 為 None 者整批跳過）
```

實測（`docs/superpowers/specs/2026-08-08-rag-retrieval-quality-design.md`）：

- 知識庫共 4,605 chunks / 942 個 URL，94% 由外部 ETL repo（`Capoo0618/CARE-data`）每日 08:00 寫入，不受本 repo 控制
- 食藥署那批 1,367 chunks（佔 KB 30%）`url` 全為 `None`；即使檢索命中，`_append_sources` 的 `if not url: continue` 也會整批跳過，無法被列為參考來源
- 現行 eval 只有 binary hit_rate，rerank 前後（vector 0.29 → cohere 0.44）的排序品質差異看不出解析度，無法回答「命中的排第幾名」
- context 只放純文字、不含來源資訊，prompt 未要求逐句標註引用編號；來源清單是事後依檢索分數貼上，與答案正文實際引用了什麼內容脫鉤

約束：

- 本 change 不改檢索演算法本身（wide retrieve / rerank 參數調整屬另一個 change `rag-retrieval-tuning`）；這裡只解決「量得準」與「來源對得上答案」
- golden set（`evals/rag/golden.jsonl`）每題只標一個正解來源，沒有窮盡的相關性判準（exhaustive relevance judgments）
- 受 `openspec/specs/line-reply-rules` 約束：LINE 回覆一律純文字、不得輸出 Markdown
- 測試依 `openspec/config.yaml` 規定禁止 monkey patch，一律以依賴注入傳入 mock

## Goals / Non-Goals

**Goals:**

- eval 指標從單一 binary hit_rate 擴充為 hit_rate（沿用舊口徑對照）＋ MRR ＋ nDCG@5，足以分辨 rerank 前後的排序品質差異
- 新增 `original_title` 標籤路徑，提供不隨切片方式改變的穩定判準
- context 帶編號與出處標頭、prompt 要求逐句標 `[n]`，讓「答案正文引用了誰」與「來源清單列出誰」一致
- 修正無 `url` 來源被靜默丟棄的問題，改以「來源名｜標題」呈現
- 新增 citation coverage 指標，量化「命中卻無法被引用」的發生率，作為安全網與後續 change 的驗收依據

**Non-Goals:**

- 不做 recall@k（見 D1）
- 不調整檢索召回策略、rerank 參數、`min_score`、embedding（屬 `rag-retrieval-tuning`）
- 不修復 ETL 端資料品質（食藥署缺 url 是上游問題，交付另一份 `docs/care-data-issues.md` 報告，非 openspec change）
- 不改變對外 `get_rag_answer` tool 介面

## Decisions

### D1. 為何不做 recall@k

`recall@k` 需要「該題在整個語料庫中共有幾筆相關文件」這個分母，而 golden set 每題只標了一個正解來源，沒有窮盡的相關性判準（exhaustive relevance judgments）。硬算出來的分母是假的，指標會失去意義，甚至誤導後續調參方向。因此改採不需要該分母、同樣能反映排序品質的兩個指標：

- `MRR`：第一筆命中的排名倒數，直接反映「有沒有排上去」
- `nDCG@5`：二元 gain 的位置加權，是 rerank 前後差異的主要觀測指標

兩者與沿用的 `hit_rate@k` 皆以既有的 substring 判準（url／title／source／content 任一命中即 gain=1）決定單篇 relevance。

### D2. nDCG 的 IDCG 基準

`nDCG@5` 的 IDCG（理想 DCG）基準以「取回清單自身的 relevance 重排後」計算，而非以語料庫全體的理想排序——理由同 D1，語料庫層級的窮盡相關性判準不存在，無法算出「全庫理想排序」。這是缺完整判準時的標準做法：取回清單內把命中的文件排到最前面即為該題的理想排序，DCG 除以這個 IDCG 得到 nDCG。

此口徑須在 `evals/rag/README.md` 明確註明，避免日後誤讀為「與全庫理想排序比較」的標準 nDCG 定義。

### D3. 未引用時不附來源

當模型完全沒有在回答中輸出任何 `[n]` 引用標記時，SHALL 不附加「參考資料來源」區塊，並記錄 `citation_missing` log。這是刻意的保守選擇：忠於「只列實際被引用者」的原則，寧可不附來源，也不要退回「附上檢索分數最高的幾筆但答案根本沒引用」的舊行為（那正是導致「答案內容與所附來源不對應」的根因）。

此決策也與既有 `no-fabricated-rag-sources` change 的意圖一致——該 change 已確立「來源僅能來自工具真實輸出，不得捏造／不得無依據地保留」的原則；D3 是在生成端加上更精細的引用比對後，把同一原則延伸到「有來源但答案沒引用」的情境。

這個決策會不會造成使用者太常看不到來源，由新增的 citation coverage 指標（Task 6）持續量測，若發生率過高則是 prompt 或 context 格式需要調整的訊號，而非放寬「未引用不附來源」這條規則。
