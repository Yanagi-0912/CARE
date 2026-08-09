# RAG Eval（小而真題庫）

用來量測知識庫檢索／回答品質，方便比較 rerank on/off、`top_n`、模型等設定。

## 題庫格式（`golden.jsonl`）

一行一題 JSON：

| 欄位 | 必填 | 說明 |
|------|------|------|
| `id` | ✓ | 穩定識別碼 |
| `query` | ✓ | 貼近 LINE 的問句 |
| `route` | ✓ | `kb` / `refuse` / `web` |
| `expected_url_substrings` | kb 建議 | 期望來源 URL 片段；**請用細標**（如 `pid=19023`），勿只用 `hpa.gov` |
| `expected_source_substrings` | 可選 | `source_name` 片段（粗；缺 url 時備用） |
| `expected_content_substrings` | kb 建議 | chunk 內必須出現的關鍵句（缺 url 時尤其重要） |
| `expected_title_substrings` | 建議 | 期望 `original_title` 片段。**最穩定的標籤** —— 不隨切片方式改變；`expected_content_substrings` 會在上游改切法時整批失效 |
| `must_not_answer` | | `true`＝應拒答／無資料 |
| `notes` | | 備註 |
| `split` | | 可選 `train` / `holdout` |

> 粗標（`hpa.gov`／`衛福部`）會讓 top-5 hit_rate 飽和、看不出精排差異。可用：
> `python scripts/rag_tighten_golden.py`  
> 自動收成 `pid=`／關鍵句（偏好向量 mid-rank 的相關 chunk）。仍建議抽樣人工覆核。

## 去識別化

- **禁止**寫入真實姓名、病歷、電話、身分證等
- 問句可改寫成通用語氣

## 怎麼補題（目標 30～50）

1. 從 LINE／常用測資收集問句  
2. 標 `route`  
3. `kb` 題先跑：

```bash
# 專案根目錄
python scripts/rag_query_cli.py "你的問句"
```

把正確來源的網域／路徑片段寫進 `expected_url_substrings`；若 url 皆空，改標 `expected_source_substrings`。  
4. 刻意加拒答、易混淆、同義改寫題  
5. 約 20% 標 `split: holdout`，調參時可先過濾

## 怎麼跑

預設只評 **retrieval hit**（`route=kb` 且有期望 substring 的題）：

```bash
python scripts/rag_eval.py
python scripts/rag_eval.py --golden evals/rag/golden.jsonl --out /tmp/rag-report.json
```

精排後 top-n（與線上 prompt 口徑一致）：

```bash
python scripts/rag_eval.py --rank-mode vector --top-n 5
python scripts/rag_eval.py --rank-mode cohere --top-n 5
```

**有／無 Cohere 對照**（同一批 wide retrieve，各取 top-n）：

```bash
python scripts/rag_eval.py --compare-rerank --top-n 5 --out /tmp/rag-compare.json
```

可選答案層：

```bash
python scripts/rag_eval.py --with-answer --out /tmp/rag-report.json
```

hit 率低於門檻時非 0 exit：

```bash
python scripts/rag_eval.py --fail-under 0.6
```

只跑 train split：

```bash
python scripts/rag_eval.py --split train
```

## 怎麼讀結果

- **hit_rate**：在「有計分」的 kb 題中，檢索（或精排後）結果的 url **或** source_name 命中期望 substring 的比例  
- **miss_ids**：沒命中的題，優先人工檢查 substring 是否標錯、或檢索真的失敗  
- `web`／無期望來源的題會 **skip**，不計入 hit_rate  
- **mean_mrr**：有計分題目的 MRR（第一筆命中文件排名的倒數，全無命中則該題為 0）平均值 —— 反映命中文件排得多前面
- **mean_ndcg_at_5**：有計分題目的 nDCG@5（二元 gain、依位置加權）平均值 —— 命中排第 1 名與排第 5 名的貢獻不同
- nDCG 的 IDCG（理想 DCG）以「取回清單自身的 relevance 重排後」計算，**不是**語料庫全體的理想排序 —— golden set 每題只標一個正解來源，沒有窮盡的相關性判準（exhaustive relevance judgments），算不出「全庫理想排序」；同理，本專案**刻意不提供 recall@k**，因為 recall 需要「該題在語料庫中共有幾筆相關文件」這個分母，硬湊出來的分母是假的、會誤導調參方向
- `--compare-rerank`：看 `hit_rate_delta`、`ndcg@5_delta`、`fixed_by_cohere`、`regressed_by_cohere`
- **citation_coverage**（需 `--with-answer`）：在「有跑答案層」的題目中（`citation_count` 不為 `None`），答案內至少標出一個有效 `[n]` 引用的比例；分母不含未跑 `--with-answer` 的題目。此指標量測模型是否確實依規範標註引用來源——過低代表 Task 4 的引用 prompt 需再強化。若答案完全沒有標 `[n]`，`_append_sources` 不會附上來源清單，並會記一筆 `citation_missing` log 供追蹤

### 本分支實測結果（本機 2026-08-08，`python scripts/rag_eval.py --rank-mode cohere --top-n 5`，golden set 34 scored cases）

> ⚠️ **舊口徑**：下表使用 2026-08-09 題庫稽核**之前**的標籤（後來證實其中多題標籤指向不相關文章）。數字僅供分支內前後對照，不可與稽核後的新基準相比。

| 階段 | hit_rate@5 | mean_mrr | mean_ndcg@5 |
| --- | --- | --- | --- |
| 分支起點 | 0.412 | 0.198 | 0.253 |
| 移除向量分數門檻後 | 0.412 | 0.198 | 0.253 |
| reranker 補標題後 | 0.382 | 0.217 | 0.257 |
| 刪除 266 筆導覽列噪音後 | **0.441** | **0.241** | **0.291** |

`citation_coverage`（需 `--with-answer`）= 1.0（34/34）。

補充事實：
- 移除向量分數門檻（Task 1）後，上面三個指標**逐位元不變**——實測 `$vectorSearch` top-40 分數全落在 0.79–0.90，無一低於原本 0.5 的門檻，代表該門檻在本分支的資料上從未真正過濾任何候選。
- reranker 補標題（Task 2）帶來的 nDCG 變化只有 +0.004，屬雜訊等級；「補標題能讓精排更準」這個假設**未獲驗證**。
- 整個分支唯一實質的指標增益來自刪除 266 筆導覽列噪音資料（當時重跑複核得到相同數字）。
- **執行間變異（後續實測修正）**：`hit_rate` 與 `miss_ids` 在重跑間穩定，但 `mean_mrr` / `mean_ndcg@5` 存在約 **±0.011–0.015** 的執行間變異（Cohere 精排順序非完全確定性）。小於此幅度的差異不應解讀為訊號。

### 題庫稽核後的新基準（2026-08-09，22 scored cases）

題庫經逐題稽核（詳見 `docs/golden-set-audit.md`）：7 題標籤指向不相關文章已改標、
12 題 KB 無可回答文章改 `route: web`、kb-005/013/018 補收近重複語料中**同等有效**的
答案文章（政府新聞稿同主題年年重發，單一正解標籤會把「撈到另一篇同樣正確的文章」
誤判為 miss）。**口徑改變，數字與上表不可直接相比。**

| 排序方式 | hit_rate@5 | mean_mrr | mean_ndcg@5 |
| --- | --- | --- | --- |
| RRF 混合排序 | 0.818 | 0.558 | 0.613 |
| Cohere rerank-v4.0-pro | **0.864** | **0.583** | **0.644** |

（`--compare-rerank --top-n 5`，同一批 wide retrieve；`regressed_by_cohere` 0 題、
`fixed_by_cohere` 1 題。文章去重（`rerank-article-dedup`）上線後 RRF 分支為 0.610，
變化屬雜訊——該改動的目的是 top-5 的來源多樣性，不是指標。）

### 名詞澄清 A ——`--rank-mode vector` 目前是誤稱

在 `RAG_HYBRID_ENABLED=true`（目前線上設定）之下，`--rank-mode vector` 用的是 `VectorScoreReranker`，它依 `metadata["score"]` 排序；但 `app/services/rag/rank_fusion.py:99` 已把該欄位**覆寫**成 RRF 融合分數（該模組的 docstring 自述這是「刻意覆寫」）。所以這個模式實際排序依據是 **「RRF 混合排序（向量 + BM25）」**，不是純向量分數。

本 README 舊版留著一筆 2026-08-01 的紀錄，方向與本分支的實測相反（`vector 0.29 → cohere 0.44`），最可能的原因是那次量測時 hybrid 尚未啟用，兩次量測的「vector」根本不是同一件事，因此已移除該筆舊紀錄。

用這個口徑看 `--compare-rerank` 的實測，**結論隨題庫標籤品質三度反轉**，完整演變：

| 題庫狀態 | ndcg@5_delta（cohere − RRF） | regressed / fixed | 當時結論 |
| --- | --- | --- | --- |
| 舊標籤（循環標註，多題錯標） | −0.190 | 13 / 2 | 「Cohere 明顯有害」 |
| 稽核修正（單一正解） | −0.022 | 2 / 1 | 「接近雜訊」 |
| 公允多標籤（承認近重複語料） | **+0.032** | **0 / 1** | **「Cohere 勝出」** |

前兩行的「regressed」後來證實幾乎全是標籤假象：Cohere 撈出同樣正確的另一篇文章被
判 miss、或同文章多 chunk 擠爆 top-5（後者已由 `rerank-article-dedup` 處理）。
Cohere 真正的獨特貢獻在細粒度語意區分（例：kb-014 分辨「中風**前兆**」與
「中風**危險因子**」，BM25 與向量都做不到）。

**教訓：這張表的結論方向完全由標籤品質決定。** 改題庫標籤之前的任何 rerank A/B
數字都要先問「標籤可信嗎」。目前公允量測下 Cohere 有正增益，續用與否的剩餘考量是
配額管理（Trial key 1,000 次/月）與降級可觀測性，不是品質。

### 名詞澄清 B ——`refuse_ok` 目前量到的是錯的層

`refuse_ok` 這個指標在 `scripts/rag_eval.py` 的輸出裡會印出來，但 README 先前完全沒有說明過它是什麼、該怎麼解讀。

`refuse_ok` 目前恆為 `0/3`，這是**預期行為，不是待修的 bug**。拒答的 guardrail 位於 LangGraph agent 層（`app/services/agent/utils/nodes.py:199` 的 `allow_rag_tool`，決定要不要把 `get_rag_answer` 這個工具交給 LLM），而 `scripts/rag_eval.py:194` 是直接呼叫 `answer_service.answer()`，**完全繞過 agent**；再加上 web fallback 預設開啟，non-medical 問題在 KB 裡撈不到東西時就會改上網生成答案，於是 eval 量到的「該拒答卻沒拒答」其實是繞過 guardrail 造成的假訊號，不代表 guardrail 本身壞了。

**不要因為這個數字，就跑去在 `RagAnswerService` 裡加一份拒答邏輯** —— 那會讓同一條安全規則出現兩份會各自演化、彼此可能不一致的實作。真要修，方向應該是讓 eval 走完整的 agent 流程再量測，或是把這三題移出 RAG golden set（交由 agent 層的測試覆蓋）；這兩者都不在本分支範圍內，留給後續 change。
