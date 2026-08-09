## Context

CARE Agent 已有院所 Flex（tool 回 `json.dumps` bubble，`reply.py` 辨識後送 FlexMessage）與 location quick reply force 模式。`LIFF_URL`、`PUBLIC_BASE_URL`（Helm 由 `public.host` 組出，如 `https://care.jamessu2016.com`）已存在。LIFF 與官網對使用者而言是同一個目的地，兩顆按鈕只會製造選擇成本，故入口卡只給一顆按鈕，優先導向 LIFF。

## Goals / Non-Goals

**Goals:**
- Tool `open_official_site` 回傳可送出的 Flex JSON
- 關鍵字／意圖穩定觸發；避免誤走 RAG
- URL 全由 settings；缺 LIFF 時仍盡量給官網

**Non-Goals:**
- 不做桌面免 LINE 登入旁路
- 不改 LIFF 前端登入流程
- 不做多語 Flex 全文（v0 可用繁中固定文案；若既有 i18n catalog 易接則可接）
- 不新增 admin／知識回報流程

## Decisions

1. **Tool 名稱** `open_official_site`  
   - 無參數；回傳 `json.dumps(flex_dict, ensure_ascii=False)`，格式對齊院所 Flex（含 `type: flex`／`contents` 結構，與現有 `_to_flex_message_text` 慣例一致）。

2. **單一入口按鈕（優先 LIFF）**  
   - 主：開啟 LIFF（`LIFF_URL`）  
   - 次：開啟官網（`PUBLIC_BASE_URL`）  
   - 若僅一端有值：只渲染有值的按鈕；兩者皆空 → 回傳簡短純文字錯誤提示（勿拋例外炸 Agent）。

3. **Force 路由**（對齊 hospital intent）  
   - `_is_official_site_intent(text)`：官網／官方網站／打開官網／打開網站／LIFF（怎麼開）等  
   - 命中且模型未 tool_call → 注入 `open_official_site`  
   - 命中 → 不 force `get_rag_answer`  
   - 媒體前綴全文仍不套用此 force（與既有 media skip 一致）

4. **Prompt**  
   - 工具優先順序新增一條：要官網／網站／LIFF 入口 → `open_official_site`，禁止 RAG、禁止只貼裸網址。  
   - Flex 原樣輸出規則擴充涵蓋本 tool。

5. **模組位置**  
   - Flex 純函式：`resources/flex_messages/...` 或 `app/services/line_messaging/flex/official_site_flex.py`（擇一與院所／medication 風格一致者）  
   - Tool：`app/tools/official_site_tools.py`＋registry

## Risks / Trade-offs

- [誤觸發] 「官網疫苗資訊」可能撞關鍵字 → Mitigation：關鍵字偏「打開／入口／怎麼開／官網連結」；衛教句仍可走 RAG；force 僅短意圖
- [PUBLIC_BASE_URL 空] → 僅 LIFF 按鈕；文件提醒 Helm 已有 public host
- [桌面仍難用 LIFF] → 官網按鈕緩解；不在本 change 解決完整桌面登入

## Migration Plan

1. 合併後部署後端即可；確認 ConfigMap 有 `LIFF_URL`、`PUBLIC_BASE_URL`
2. LINE 實測：「打開官網」→ Flex 單顆按鈕，點擊進入 LIFF
3. Rollback：關閉 force＋自 registry 移除 tool（或 feature 未做 flag 則 revert commit）

## Open Questions

- （無；v0 繁中文案固定）
