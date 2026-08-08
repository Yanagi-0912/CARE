## Why

> **回溯記錄（Retroactive）**：本 change 的實作與測試已於 2026-08-08 完成並通過（pytest 804 全綠），
> 但當時直接編輯了 `openspec/specs/location-search/spec.md`，跳過了 change 流程。
> 本文件補齊該次變更的 proposal／design／specs／tasks，使規格演進有完整可追溯的紀錄。
> 因 `specs/` 已同步，歸檔時須使用 `openspec archive --skip-specs` 避免需求重複併入。

原本的鄰近院所搜尋只能回傳「附近的醫療院所」，無法依科別查詢。使用者說「附近有腸胃科嗎」時，
系統會回傳一份混雜牙科、婦產科的清單，實用性低。

同時發現三個實質缺陷：

1. **資料落差**：`medicalFacilities.departments` 僅含 55 個衛福部部定專科，**不含任何次專科**。
   使用者常說的「腸胃科」「心臟科」「腎臟科」在資料庫中並不存在，皆隸屬「內科」。
2. **科別意圖跨輪遺失**：使用者提出科別需求時通常尚無座標，系統會先請其分享位置；
   座標訊息進入對話後，`_latest_human_text()` 只取最後一則訊息（即座標文字），科別資訊已遺失，
   導致退化為不分科別搜尋。
3. **硬 5 公里上限**：偏遠地區 5 公里內查無院所即回「查無資料」，而非放寬範圍。

## What Changes

- 新增科別搜尋工具 `find_nearby_facilities_by_department(lat, lng, department)`。
- 新增 `app/services/medical/department_matcher.py`：俗稱／次專科 → 部定專科映射表（約 100 條）。
  刻意**不做症狀分診**（如「胸悶」→ 內科），該行為屬醫療判斷，猜錯可能將需急診者導向一般門診。
- 科別比對改用 regex 而非精確比對：約 12 筆院所（多為醫學中心，如羅東博愛醫院、成大斗六分院）
  的 `departments` 為「整串科別擠在單一陣列元素」的髒資料，精確比對會將其漏掉。
- 鄰近搜尋改為逐級擴大 5→10→20→50 公里，直到湊滿目標筆數（預設 5 筆）；
  50 公里仍不足則回傳已找到的部分，而非「查無資料」。**不分科別與依科別兩條路徑共用同一套分級。**
- 修正科別意圖跨輪遺失：新增 `_extract_department_from_history()` 回溯最近 4 則使用者訊息。
- 新增 `_is_nearby_department_intent()`：「附近有腸胃科嗎」不含「醫院／診所」字眼，
  原本無法觸發位置請求流程。要求同時出現鄰近詞，以排除「我牙齒痛」這類純症狀敘述。
- 名稱查詢改為就近優先（50 公里）、查無則放寬全國。**不可只加硬上限** ——
  高雄使用者查「臺大醫院」會從「找得到」變成「查無資料」。
- 回覆誠實揭露搜尋範圍。擴大範圍時報「結果中最遠院所的實際距離」而非階梯級距：
  級距 50 公里但最遠院所僅 27 公里時，報級距會使使用者高估交通成本。
- 別名映射時明確告知使用者對應關係（「腸胃科」在健保資料中歸類於「內科」）。
- 修正 `location.no_facility` 文案：原文寫「附近 5 公里內」（範圍已變）且「功能仍在建置中」（已非事實）。

## Capabilities

### New Capabilities

- （無）

### Modified Capabilities

- `location-search`：新增依科別搜尋、逐級擴大搜尋範圍、科別意圖跨輪保留、
  名稱查詢就近優先、誠實揭露搜尋範圍等需求。
- `agent-architecture`：工具集新增 `find_nearby_facilities_by_department`；
  座標進入對話時的強制注入邏輯改為依歷史科別決定呼叫哪一個搜尋工具。

## Impact

- **新增程式**：`app/services/medical/department_matcher.py`
- **修改程式**：`app/repositories/medical_facility_repository.py`（`find_near` 加 `query`、
  `find_by_query_near` 加 `max_distance_meters`）、`app/services/medical/medical_service.py`、
  `app/tools/medical_tools.py`、`app/tools/registry.py`、`app/services/agent/utils/nodes.py`、
  `app/services/agent/agent.py`、`app/services/agent/prompt.py`、`app/i18n/messages.py`、
  `resources/flex_messages/medical_messages/facility_brief_flex_message.py`
- **API/route**：無新增或變更 route。變更僅在 LINE webhook（`/line/callback`）的對話行為層。
- **BREAKING（內部介面）**：`MedicalService.find_nearby_hospitals()` 簽名由
  `(lat, lng, radius_meters=5000, limit=5) -> list[MedicalFacility]` 改為
  `(lat, lng, target_count=5) -> NearbySearchResult`。僅內部呼叫端受影響，已同步更新。
- **資料庫**：無 schema 變更、無新增索引。科別過濾併入既有 `$geoNear` 的 `query`，
  仍由 `location_2dsphere` 索引服務。
- **測試計畫**：見 tasks.md。單元測試 `tests/unit/services/medical/`、
  `tests/unit/services/agent/`、`tests/unit/tools/`；整合測試 `tests/integration/test_medical_service_db.py`。
