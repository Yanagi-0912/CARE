## Why

加入 Cohere rerank（以及之後 CRAG）前，CARE 沒有可重複跑的 RAG 評測題庫，無法客觀比較 `pro`/`fast`、`top_n=5/8` 等設定。需要一套「小而真」的 golden set + 可在本機／CI 跑的粗指標腳本，讓每次 pipeline 改動都有分數可看。

## What Changes

- 新增評測資料格式與種子題庫檔（JSONL，目標 30–50 題；先放 schema + 少量範例，其餘由真實問句補齊）。
- 新增本機評測腳本：對每題跑檢索（可選完整 `RagAnswerService.answer`），輸出粗指標與每題明細。
- 粗指標先做三類：**檢索是否撈到期望來源**、**答案是否胡謅／空答**、**來源列表是否對得上**（不做完整 RAGAS 堆疊）。
- 文件說明：如何從 LINE／log 抽題、如何標註、如何解讀報告。
- **非 BREAKING**：不改線上 API／LINE 行為；僅開發與品質驗證工具。

## Capabilities

### New Capabilities

- `rag-eval`：定義 RAG golden set 格式、標註欄位、評測腳本行為與最低指標集合。

### Modified Capabilities

- （無）不修改 `rag-responses` 執行期行為；評測為旁路工具。

## Impact

- **新增**：`evals/rag/`（或 `tests/eval/rag/`）資料與 README；`scripts/rag_eval.py`（或同等）
- **相依**：重用既有 `MongoAtlasVectorRetriever`／`RagAnswerService`；可選呼叫 Cohere（若已實作 rerank）
- **CI**：預設不強制打真實 Mongo／Cohere；可提供 `--offline-fixture` 或標記為手動／nightly
- **人員流程**：需要有人從真實問句補齊 30–50 題標註（此為資料工作，非純程式）
