# CARE 複合問題評估題庫

這份題庫用來比較「整則訊息檢索」與「先拆子問題再檢索」：真複合題檢查拆題的效益，單一需求題檢查誤拆風險。它提供預期需求數、獨立子問題、固定知識庫可否回答及可接受的文章標題片段，不含模型作答或實驗成效。

`golden.jsonl` 共 **1,000 筆**：500 題基本題及各一個變體。

| 類別 | 基本題 | 變體 | 合計 |
|---|---:|---:|---:|
| `true_compound`：2–3 個不同資訊需求 | 300 | 300 | 600 |
| `single_need`：多句追問同一需求 | 200 | 200 | 400 |

每種類型的變體各 125 題，含 75 題真複合與 50 題單一需求：`colloquial`（口語）、`taigi`（台語口吻）、`stt_typo`（語音輸入／錯字）、`no_punct`（無標點）。改寫只產生問題文字；類別、需求數、子問題及來源標籤均由程式從基本題複製。

## 使用方式

在 CARE 專案根目錄執行，僅需 Python 標準函式庫：

```bash
python3 evals/rag_compound/validate.py
```

同時核對標題片段與完整固定 KB：

```bash
python3 evals/rag_compound/validate.py \
  --kb-chunks ../output/rag_compound_build_20261029/kb_snapshot_20261029/chunks.jsonl
```

KB 快照保存在本次工作區的 `output/rag_compound_build_20261029/`，未將整份語料重複加入專案。移到其他環境時，使用相同 SHA-256 的 `chunks.jsonl`，並以 `--kb-chunks` 指定其位置。快照資訊與雜湊見 [audit/kb_snapshot_manifest.json](audit/kb_snapshot_manifest.json)。

`split` 為 `train` 或 `holdout`，分別 800／200 筆。拆分以基本題為單位，同一題的變體跟隨基本題，避免成對資料跨組；holdout 的 100 題基本題含 60 題真複合與 40 題單一需求。這不是依疾病或文章分組的外推測試，不保證各組完全沒有共用主題或文章。拆分種子與檔案雜湊記在 [audit/manifest.json](audit/manifest.json)。比較方法時應固定題目、KB 與檢索設定，並以基本題配對比較原文／變體，避免把它們當成互相獨立的樣本。

## 每筆資料

- `id`、`query`：題號與使用者訊息。
- `category`、`expected_intents`：以證據與需求判定，不能只計算問號。
- `subquestions`：`sid`、可獨立理解的 `ask`、`route`、`expected_title_substrings` 及證據 `notes`。
- `variant_of`、`variant_type`：基本題為 `null`；變體指向唯一基本題。
- `source_wid`、`source`、`golden_id`、`topic`：作者批次與既有題目的溯源資訊。
- `review`：另一位代理的標註複核紀錄。變體另有 `variant_review_notes`，不把改寫誤稱為重新標註。

`route: kb` 表示 **2026-10-29 固定快照**內有文章直接回答全部或部分核心；只要一句實際回答核心，也納入可接受來源。它不代表該文章涵蓋所有追問。`route: web` 表示此固定 KB 未找到足以回答核心的內容，不表示現實世界沒有答案，也不表示已驗證某個外部網頁。

標題片段採字面子字串比對，不含空白；每片段至多匹配 5 篇相關文章，每個子問題的可接受文章聯集不超過 20 篇。[audit/expected_articles.jsonl](audit/expected_articles.jsonl) 逐題列出快照文章 ID 及標題片段正規化前後的映射；正規化不增減原先已複核的匹配文章集合。快照內相似或重刊文章可能同時可接受，不能把單一字面片段當成文章主鍵。

## 建置與複核

沿用 Claude 先前未看 KB 的題目草稿及部分既有標註，再由 Codex 與獲使用者授權的子代理接續。每個需求使用原句、術語與同義正規式檢索，閱讀候選全文；作者與最終標註複核者不同。複核會修正漏標、移除僅出現在謠言正文的內容、還原被改動的年齡／疾病／時序條件，並剔除尚有爭議或來源集合過廣的題目。變體由 Codex 代理製作，無標點版本以程式處理並逐筆比對；另抽查其他改寫的語意、否定與數值條件。未使用真實 LINE 對話。

- [audit/annotation_provenance.jsonl](audit/annotation_provenance.jsonl)：選用題的原始標註、作者、獨立複核者及來源版本雜湊。
- [audit/candidate_decisions.jsonl](audit/candidate_decisions.jsonl)：候選題的接受、待修或拒收紀錄；未選用不一定代表錯誤，也可能是已達配額。
- [audit/validation_report.json](audit/validation_report.json)：數量、分組、標籤一致性、標題匹配與數值檢查結果。
- [audit/SPEC.md](audit/SPEC.md)、[audit/LABEL_PROMPT.md](audit/LABEL_PROMPT.md)：沿用的建置規格；內含原 Claude 工作區路徑，屬溯源紀錄。

這是機器生成、由不同代理交叉複核的評估資料，尚未經人類臨床專家審查。正式研究前可由 James／組員抽查約 10%，特別留意部分核心的邊界、年代不同的政策及語體自然度。程式驗證不等同醫學正確性驗證，這份題庫也不是醫療建議。
