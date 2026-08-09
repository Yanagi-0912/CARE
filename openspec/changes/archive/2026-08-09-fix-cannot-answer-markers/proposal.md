## Why

Prod 已應證：河魨衛教答案含「無法透過加熱破壞」被裸 marker `無法` 誤判為 `MODEL_REFUSE`，正確答案被丟棄。

## What Changes

- 移除裸 `無法`；改為更精準拒答片語（如「無法提供」「無法回答」「無法安全回答」等）。
- 將 `CANNOT_ANSWER_MARKERS` 收斂為 KB／Web 共用單一來源（避免兩份清單漂移）。
- 補回歸測試：含「無法透過加熱破壞」的完整衛教 SHALL NOT refuse；真拒答片語仍 refuse。

## Capabilities

### New Capabilities

- （無）

### Modified Capabilities

- `rag-responses`：無法回答啟發式不得因一般敘事中的「無法」誤殺可用答案。

## Impact

- **程式**：`cannot_answer.py`、`answer_service.py`、`web_search_service.py`、相關測試
- **行為**：誤殺案例會正常回傳答案＋來源；真拒答仍回 MODEL_REFUSE
- **測試**：單元測試覆蓋誤殺／真拒答
