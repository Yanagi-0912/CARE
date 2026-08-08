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
- 細標後範例（本機 2026-08-01）：top-5 vector **0.29** → cohere **0.44**（delta **+0.15**）
- **citation_coverage**（需 `--with-answer`）：在「有跑答案層」的題目中（`citation_count` 不為 `None`），答案內至少標出一個有效 `[n]` 引用的比例；分母不含未跑 `--with-answer` 的題目。此指標量測模型是否確實依規範標註引用來源——過低代表 Task 4 的引用 prompt 需再強化。若答案完全沒有標 `[n]`，`_append_sources` 不會附上來源清單，並會記一筆 `citation_missing` log 供追蹤
