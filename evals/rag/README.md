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
- `--compare-rerank`：看 `hit_rate_delta`、`fixed_by_cohere`、`regressed_by_cohere`
- 細標後範例（本機 2026-08-01）：top-5 vector **0.29** → cohere **0.44**（delta **+0.15**）
