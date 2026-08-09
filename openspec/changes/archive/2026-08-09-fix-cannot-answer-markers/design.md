## Context

診斷 log：`matched_marker=無法`，preview 為完整河魨中毒衛教（含「無法透過加熱破壞」）。

## Goals / Non-Goals

**Goals:** 消除裸 `無法` 誤殺；清單單一來源；回歸測試。  
**Non-Goals:** 改 CRAG／web fallback 策略；改 fail 文案；改診斷 log 格式。

## Decisions

1. **共用常數**  
   `CANNOT_ANSWER_MARKERS` 定義於 `app/services/rag/cannot_answer.py`；`answer_service`／`web_search_service` 從此匯入（可 re-export 以相容舊測試匯入）。

2. **新清單（精準片語，至少）**  
   - 中文：`不知道`、`無法提供`、`無法回答`、`無法安全回答`、`未找到`、`找不到相關`  
   - 保留既有英／日文較具體片語（don't know、cannot answer、unable to answer、not enough information、no matching、わかりません、答えられません）  
   - **禁止**單獨的 `無法`

3. **回歸案例（必須測）**  
   - `"……結構穩定，無法透過加熱破壞……"` → `_is_cannot_answer` False  
   - `"根據現有資料無法提供建議。"` → True（matched `無法提供`）  
   - `"我不知道"` → True

4. **更新** 既有測試中 assert `matched_marker=無法` 改為 `無法提供`（若仍用該例句）。
