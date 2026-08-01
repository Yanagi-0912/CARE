# RAG Eval（小而真題庫）

用來量測知識庫檢索／回答品質，方便比較 rerank on/off、`top_n`、模型等設定。

## 題庫格式（`golden.jsonl`）

一行一題 JSON：

| 欄位 | 必填 | 說明 |
|------|------|------|
| `id` | ✓ | 穩定識別碼 |
| `query` | ✓ | 貼近 LINE 的問句 |
| `route` | ✓ | `kb` / `refuse` / `web` |
| `expected_url_substrings` | `kb` 建議有 | 期望來源 URL 需包含的片段 |
| `must_not_answer` | | `true`＝應拒答／無資料 |
| `notes` | | 備註 |
| `split` | | 可選 `train` / `holdout` |

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

把正確來源的網域／路徑片段寫進 `expected_url_substrings`。  
4. 刻意加拒答、易混淆、同義改寫題  
5. 約 20% 標 `split: holdout`，調參時可先過濾

## 怎麼跑

預設只評 **retrieval hit**（`route=kb` 且有期望 substring 的題）：

```bash
python scripts/rag_eval.py
python scripts/rag_eval.py --golden evals/rag/golden.jsonl --out /tmp/rag-report.json
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

- **hit_rate**：在「有計分」的 kb 題中，檢索結果 url 命中任一期望 substring 的比例  
- **miss_ids**：沒命中的題，優先人工檢查 substring 是否標錯、或檢索真的失敗  
- `web`／無期望來源的題會 **skip**，不計入 hit_rate  

比較設定時：改 env（例如未來的 `RAG_RERANK_TOP_N`）後重跑，對照兩份 report 的 `hit_rate` 與 `miss_ids`。
