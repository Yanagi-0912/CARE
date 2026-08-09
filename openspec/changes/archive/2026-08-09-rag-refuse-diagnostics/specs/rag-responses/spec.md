## ADDED Requirements

### Requirement: MODEL_REFUSE 診斷日誌

當系統因生成內容符合「無法回答」啟發式而回傳 `MODEL_REFUSE` 時，SHALL 寫入一筆診斷 log，至少包含：

- `matched_marker`：觸發啟發式的 marker（內容為空時以明確 empty 標記表示）
- `answer_preview`：生成原文的截斷預覽（長度上限由實作固定，建議 200 字元）

此要求適用於知識庫生成路徑與 Web fallback 生成路徑。系統 SHALL NOT 因診斷 log 改變對外回傳的 fail 文案或成功／失敗判定結果。

#### Scenario: KB 生成被 marker 攔截

- **WHEN** 知識庫路徑生成文字含標記「無法」且因此回傳 MODEL_REFUSE
- **THEN** log 含 `matched_marker` 對應「無法」，且 `answer_preview` 含該生成文字之前綴

#### Scenario: 生成為空字串

- **WHEN** 生成結果為空或僅空白因而 MODEL_REFUSE
- **THEN** log 以明確 empty 標記表示 `matched_marker`（例如 `<empty>`）
