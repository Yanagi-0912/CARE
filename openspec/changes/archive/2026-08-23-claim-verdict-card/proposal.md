## Why

CARE 是闢謠機器人，但目前對「網傳 X 是真的嗎」這類查核型問句的回答，與一般衛教問答走完全相同的路徑，輸出是一段需要自行解讀的敘述，沒有明確結論。線上實測：

```
❓ 網傳吃鳳梨心可以溶解血栓，是真的嗎？
→「⋯並未說明吃鳳梨心可以溶解血栓。因此⋯無法判斷⋯是否為真。」
   參考來源 [1]《抗凝血劑使用注意事項》——與鳳梨無關
```

使用者（多半是替家中長輩查證的照顧者）要的是一個能直接轉給對方看的結論，不是一段閱讀理解題。

同時，知識庫裡已經有專業查核組織標註好的判定卻沒有被使用：台灣事實查核中心（TFC，IFCN 認證，發布前需至少三人核可）的每一篇報告都帶 `verdict`（錯誤／部分錯誤／正確／事實釐清／證據不足）與 `claim`（被查核的主張）兩個欄位，由 CARE-data 於 2026-08-17 起寫入每個 chunk。現行檢索完全忽略這兩個欄位。

## What Changes

- **新增 Agent tool `verify_claim`**：與 `get_rag_answer` 並列。代理選擇工具的行為本身即為意圖分流，不需要額外的分類器呼叫。
- **新增 `ClaimVerificationService`**：四段流程——主張正規化 → 比對已查核主張 → （未命中時）一般證據檢索 → 產生結構化結果。
- **判定值一律來自 TFC 的人工標註，LLM SHALL NOT 產生判定。** LLM 只負責兩件事：把使用者問句正規化成可查核的主張、把查核報告改寫成白話理由。
- **新增 Flex 判定卡**：明確判定 + 主張 + 理由 + 來源連結，並標示判定出自 TFC 而非 CARE。
- **eval 擴充**：`golden.jsonl` 新增可選 `expected_verdict`；新增判定正確率與誤配率兩個指標。
- `get_rag_answer` 與一般衛教問答路徑**不變**。

## Capabilities

### New Capabilities

- `claim-verification`：查核型問句的主張正規化、已查核比對、判定產生與呈現。

### Modified Capabilities

- `agent-architecture`：工具集新增 `verify_claim`。
- `rag-eval`：golden set 新增 `expected_verdict` 欄位與對應指標。

## Impact

- **程式**：新增 `app/services/rag/claim_verification/`、`app/tools/claim_tools.py`、`app/services/line_messaging/flex/verdict_flex.py`；修改 `app/tools/registry.py`、`app/dependencies.py`、`app/services/rag/eval_scoring.py`
- **資料**：僅讀取 CARE-data 已寫入的 `verdict` / `verdict_slug` / `claim` 欄位，本 change 不改變任何寫入行為
- **索引**：沿用既有 `MONGODB_VECTOR_INDEX`，不另建 `claim` 專用向量索引（見 design.md 決策 2；初稿曾規劃另建索引，實作時發現走不通，詳見該決策的說明）
- **行為**：非查核型問句完全不受影響；查核型問句在 TFC 未查核過時回「證據不足」，較現行的模糊敘述更保守
- **測試**：`tests/unit/services/rag/test_claim_verification.py`、`tests/unit/tools/test_claim_tools.py`、`tests/unit/services/line_messaging/flex/test_verdict_flex.py`、`tests/unit/eval/test_rag_eval_scoring.py`
- **設定**：`CLAIM_MATCH_MIN_SCORE`、`CLAIM_VERIFICATION_ENABLED`（default true）

## 尚未量測的前提

本 change 的價值完全取決於**覆蓋率**——真實謠言問句能命中 TFC 已查核主張的比例。撰稿時 TFC 資料仍在回填（294/約 600 篇），該數字尚未量測。tasks 的第 1 節即為量測，**若覆蓋率低於 30% 應重新評估本 change 的優先序**，而非直接實作。

**結論（tasks 1.1/1.2，2026-08-18 量測，完整數據見 `coverage.md`）**：知識庫回填完成後（TFC 802 篇，判定覆蓋率 100%），覆蓋率（判定正確率）為 **78%**（門檻 0.86），停止條件（< 30%）未觸發，本 change 依計畫繼續實作。門檻校準初版只看正樣本命中率、未看誤配率，後續依 design.md 決策 9 補上同一性驗證後，誤配率由 65% 降至 10%，詳細數字與已知限制見 `coverage.md`。
