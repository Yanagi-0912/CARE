## Why

「我肚子好痛要掛哪一科」是最常見的求助句型之一，目前 CARE 沒有對應路徑。實際行為是：

1. `_is_nearby_department_intent()` 要求句中同時出現鄰近詞，純症狀敘述被排除，不觸發找院所流程。
2. 落到 `SYSTEM_PROMPT` 規則 (g)：症狀 → `get_rag_answer`，由 RAG 產生一段衛教敘述。

於是使用者問的是「掛哪一科」，拿到的是「腹痛的可能原因與注意事項」。若模型在敘述中順口提到科別，那句話沒有經過任何對照表、也不保證對得上 `medicalFacilities.departments` 的 55 個部定專科——後續就算使用者想找院所也接不上。

這道缺口是**刻意留下的**：`2026-08-08-department-aware-nearby-search` 明列 Non-Goal「不做症狀分診」，理由是猜錯會把可能需要急診的人導向一般門診。該紅線同時寫在 `department_matcher.py` 模組註解與 `llm_term_resolver.py` 的 `_SAFETY_RULES` 第 3 條。

本 change **推翻該 Non-Goal**，但不是推翻其理由。原決策把問題設定成「要不要斷言分診」，答案正確地是不要；本 change 把問題重設為「使用者已經在問了，系統是沉默、亂答，還是在明確的安全邊界內給方向」。差別在於輸出形態：**建議方向而非診斷結論、多候選而非單一答案、急迫度判斷優先於任何科別建議、判斷不出來就誠實說不知道**。

## What Changes

- **新增 Agent tool `suggest_department_for_symptom`**：與 `get_rag_answer` 並列，處理「症狀 + 問科別」的問句。純症狀敘述（「我肚子好痛」）不含掛號意圖者行為不變，仍走 `get_rag_answer`。
- **新增 `UrgencyClassifier`**：語意急迫度判斷，判準為「所述狀況是否正在發生、且是否需要立即處置」。**掛在 graph 上 `agent` 之前**，判定為緊急時短路整條流程，不進 agent、不跑 RAG、不呼叫任何工具。與掛號意圖、症狀描述、工具呼叫皆無關（design 決策 1、2）。
- **新增 `SymptomDepartmentService`**：三段流程——症狀詞正規化 → 對照表比對 → 產生候選科別建議。本服務不做急迫度判斷（design 決策 3）。
- **新增人工審定的症狀對照表**：`resources/symptom_department_table/`。原料是來源醫院公開對照表的原文備份（`raw/*.md`），人工整併成 `symptom_department_reference.json`；repo 中沒有爬蟲腳本。表中只收來源所載的對應，不含本專案補列或人工排序（design 決策 14）；審定狀態以整張表的 `status` 表示。
- **對照表的科別欄位一律先過 `resolve_department()` 轉成部定專科**，載入時驗證，對不上即失敗。否則會產生「系統說查過了但附近沒有」——`llm_term_resolver.py` 模組註解指出這比「系統看不懂」更糟。
- **輸出一律為多候選 + 保底 + 免責**，並可直接銜接既有的 `find_nearby_facilities_by_department`。
- `get_rag_answer`、`find_nearby_hospitals`、`find_nearby_facilities_by_department` 與 `department_matcher` 的別名表**行為不變**。唯一例外（2026-09-14）：保底卡的按鈕要一次搜尋全部保底科別，`find_nearby_facilities_by_department` 因此改收科別清單、`department_matcher` 能從一句話解析出多個科別；別名表本身不變（design 決策 13）。`department_matcher` 維持不收症狀詞——症狀邏輯全部收斂在新模組，兩者職責不混。

## Capabilities

### New Capabilities

- `symptom-department-guidance`：急迫度判斷與短路、症狀詞正規化、症狀→候選科別建議與其呈現邊界。

### Modified Capabilities

- `agent-architecture`：工具集新增 `suggest_department_for_symptom`。
- `location-search`：釐清「純症狀敘述不請求位置」與「症狀＋掛號意圖走建議工具」的分界；建議產生後使用者要找院所時的銜接。
- `line-reply-rules`：科別建議回覆的免責與用語邊界。

## Impact

- **程式**：新增 `app/services/medical/symptom_classification/`（`urgency.py`、`normalizer.py`、`symptom_table.py`、`symptom_department_service.py`）、`app/tools/symptom_tools.py`；修改 `app/tools/registry.py`、`app/dependencies.py`、`app/services/agent/prompt.py`、`app/services/agent/utils/nodes.py`、`app/services/agent/agent.py`（graph 新增 `emergency` 節點與條件邊）、`app/services/agent/utils/state.py`
- **資源**：`resources/symsptom_department_table/` 已更名為 `resources/symptom_department_table/`（2026-09-12）；新增人工審定後的正式表
- **資料庫**：無 schema 變更，不新增 collection，不寫入任何資料
- **行為**：純症狀敘述、找院所、RAG 衛教三條既有路徑皆不受影響；新路徑僅在「症狀 + 掛號意圖」同時成立時啟用
- **測試**：`tests/unit/services/medical/test_urgency_classifier.py`、`test_symptom_normalizer.py`、`test_symptom_table.py`、`test_symptom_department_service.py`、`test_emergency_condition_flex.py`、`test_symptom_department_flex.py`、`tests/unit/tools/test_symptom_tools.py`、`tests/unit/tools/test_registry.py`（更新），以及 `tests/unit/services/agent/test_urgency_routing.py`——**端到端路由測試為必要項**：初版的單元測試全綠而線上完全失效，缺的正是這一層
- **設定**：無新增設定。不設功能旗標，隨部署上線（design 決策 10）；比對門檻為模組常數，不進 env（design 決策 12）

## 尚未量測的前提

本 change 能不能做，取決於兩個數字，都尚未量測：

1. **急症召回率**——真實急症敘述被攔下的比例。這是安全指標，**測試集上漏放率必須為 0**；達不到即停止本 change。這不是可以用「整體準確率高」補償的指標。判斷器改為語意模型後，此項量測從「可選」變成「必要」——沒有離線地板可以兜底。
2. **對照表覆蓋率**——真實口語症狀問句能命中已審定條目的比例。**低於 40% 應重新評估優先序**，因為覆蓋率太低時使用者多數仍落到保底回覆，收益不足以抵銷新增一條醫療建議路徑的風險。

量測方法與結果記錄見 tasks 第 1 節與 `coverage.md`。**在該節完成前不得進入實作**。

## 已知限制（撰稿時即成立，非實作缺陷）

- 現有原料為三家醫院（臺北榮總玉里分院、成大醫院、台大雲林分院），分科方式與粒度彼此不一致（榮總玉里把兒童症狀整列收在「15歲以下兒童」底下），科別名稱皆須轉為部定專科。表中缺成人科別的症狀（嘔吐、慢性咳嗽）與三家都沒收錄的症狀（流鼻水、流鼻血、痰多、帶狀皰疹）目前走保底，等併入更多醫院後補齊（design 決策 14）。
- 原料含來源網站錯字（「穿恐」應為「穿孔」、「坐骨神精痛」應為「坐骨神經痛」）與非科別值（`15歲以下兒童` 被解析為 department）。
- 爬蟲的 `emergency` 旗標為關鍵字初篩，明顯過寬：「常見疾病的診治（如潰瘍、便秘、感冒、頭痛等）」因含「潰瘍」被標為 `true`。腳本註解已載明此為初篩、須人工複查，本 change 據此要求人工定案。
- 「肚子痛」三字不在原料中（原料為「腹脹」「腹瀉」「下腹痛」「急性神經腹痛」等），需靠正規化與同義詞處理。
- 腹痛本身跨內科、外科、婦產科、泌尿科、急診——這正是原 Non-Goal 的立論。本 change 以「多候選 + 急迫度優先」回應，而非宣稱能分辨。
- 爬蟲註解已載明不逐字散布來源網站文字，正式表的病症描述須人工改寫。
