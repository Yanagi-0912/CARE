> 回溯記錄：以下項目均已於 2026-08-08 完成，`python -m pytest` 804 項全綠。

## 1. 科別映射

- [x] 1.1 新增 `app/services/medical/department_matcher.py`：`CANONICAL_DEPARTMENTS`（55 個部定專科）、
      `DEPARTMENT_ALIASES`（約 100 條俗稱／次專科映射）、`resolve_department()`、
      `extract_department_intent()`、`build_department_query()`
- [x] 1.2 測試 `tests/unit/services/medical/test_department_matcher.py`：
      別名解析、缺「科」字補全、長詞優先、髒資料 regex 命中、
      `test_all_alias_targets_exist_in_database` 防止映射到不存在的科別

## 2. Repository 與 Service 的階梯搜尋

- [x] 2.1 `app/repositories/medical_facility_repository.py`：`find_near()` 新增 `query` 參數，
      併入 `$geoNear` 內部（非取回後過濾，否則會漏掉稍遠但符合條件的院所）
- [x] 2.2 `app/services/medical/medical_service.py`：新增 `NEARBY_SEARCH_STEPS`、
      `NearbySearchResult`、`DepartmentSearchResult`、`_search_tiered()`、`_resolve_search_tier()`
- [x] 2.3 `find_nearby_facilities_by_department()`：解析科別 → 階梯搜尋 → 回傳含分級脈絡的結果
- [x] 2.4 `find_nearby_hospitals()` 改用同一套 `_search_tiered()`（簽名改為
      `(lat, lng, target_count) -> NearbySearchResult`）
- [x] 2.5 測試 `tests/unit/services/medical/test_department_search.py`（7 項）：
      5 公里足夠不擴大、擴大到 20 公里、50 公里湊不滿回傳部分、DB 只打一次、
      無法解析科別不查 DB、自訂 target_count
- [x] 2.6 測試 `tests/unit/services/medical/test_nearby_search.py`（6 項）：
      不分科別的階梯行為、偏鄉回傳部分結果、
      `test_general_and_department_search_share_the_same_tiers` 防止兩條路徑再度分岔

## 3. 名稱查詢就近優先

- [x] 3.1 `find_by_query_near()` 新增 `max_distance_meters` 參數（`None` 為全國）
- [x] 3.2 `find_facility_by_name()`：先以 `NAME_SEARCH_RADIUS_METERS`（50 公里）查詢，
      0 筆時放寬為全國
- [x] 3.3 測試 `tests/unit/services/medical/test_name_search_radius.py`（4 項）：
      就近命中不放寬、無結果時放寬（高雄查臺大醫院）、全國亦無結果、無座標不走 geo 路徑
- [x] 3.4 更新 `tests/unit/services/medical/test_medical_service.py` 的
      `FakeMedicalFacilityRepository` 簽名

## 4. 工具層與代理接線

- [x] 4.1 `app/tools/medical_tools.py`：新增 `find_nearby_facilities_by_department` 工具、
      `_build_range_subtitle()`、`_furthest_km()`
- [x] 4.2 `app/tools/registry.py` 註冊新工具
- [x] 4.3 `app/services/agent/agent.py` 的 `medical_tool_names` 加入新工具（避免模型改寫 Flex JSON）
- [x] 4.4 `app/services/agent/prompt.py`：規則 5(b) 改為依科別選工具、補反例、
      規則 7／9 加入新工具名
- [x] 4.5 測試 `tests/unit/tools/test_range_subtitle.py`（5 項）：
      擴大時報實際最遠距離而非級距、湊不滿時報搜尋上限、別名附註、不分科別無 match 屬性不爆炸

## 5. 跨輪科別意圖

- [x] 5.1 `app/services/agent/utils/nodes.py`：新增 `_extract_department_from_history()`、
      `_is_nearby_department_intent()`、`_PROXIMITY_RE`；`_LOCATION_TOOL_NAMES` 加入新工具
- [x] 5.2 座標強制注入路徑改為依歷史科別決定注入哪一個搜尋工具
- [x] 5.3 測試 `tests/unit/services/agent/test_department_intent.py`（16 項）：
      跨輪帶入科別、無科別走一般搜尋、過時科別不沿用、純症狀不觸發位置請求

## 6. i18n 與 Flex

- [x] 6.1 `app/i18n/messages.py`：新增 `location.department.title`／`alias_note`／`none`／`unknown`、
      `location.nearby.found_within`／`expanded`／`partial`，六語言
- [x] 6.2 修正 `location.no_facility`：範圍改為參數化、移除「功能仍在建置中」、補上 119 提示
- [x] 6.3 `resources/flex_messages/medical_messages/facility_brief_flex_message.py`：
      新增 `title_override`／`subtitle_override` 參數

## 7. 驗證

- [x] 7.1 `python -m pytest` 全綠（804 項）
- [x] 7.2 對真實資料庫驗證：台北車站（5 公里湊滿不擴大）、玉山山區（擴大至 50 公里級距）、
      蘭嶼（僅 1 家，回傳部分）、東沙外海（0 筆，走 119 文案）
- [x] 7.3 對真實資料庫驗證髒資料：regex 比精確比對多命中 9 筆醫學中心
- [x] 7.4 更新 `tests/integration/test_medical_service_db.py` 以配合新簽名

## 8. 規格同步

- [x] 8.1 `openspec/specs/location-search/spec.md` 已直接更新（本 change 為回溯補齊）
- [ ] 8.2 歸檔時使用 `openspec archive department-aware-nearby-search --skip-specs`
      （`specs/` 已同步，避免需求重複併入）
