## 1. 類型分類表

- [x] 1.1 新增 `app/services/medical/facility_type_matcher.py`：
      `CANONICAL_FACILITY_TYPES`（17 個資料庫實際 `type` 值）、
      `FACILITY_TYPE_CATEGORIES`（醫院／診所／藥局 → `type` 值集合）、
      `FACILITY_TYPE_ALIASES`（大醫院／大型醫院／住院 → 醫院；小診所／門診 → 診所；藥房／藥店 → 藥局）
- [x] 1.2 `resolve_facility_type(text) -> FacilityTypeMatch | None`、
      `extract_facility_type_intent(text)`、`build_facility_type_query(category)`（用 `$in`）
- [x] 1.3 測試 `tests/unit/services/medical/test_facility_type_matcher.py`：
      三類解析、別名、無法解析回 `None`、
      守門測試斷言 `FACILITY_TYPE_CATEGORIES` 的每個值都在 `CANONICAL_FACILITY_TYPES` 內（決策 4）
- [x] 1.4 測試：「牙醫」「中醫」SHALL NOT 被解析為類型（決策 2，避免與科別維度重複疊加）

## 2. Service 層的條件組合

- [x] 2.1 `app/services/medical/medical_service.py`：新增私有 helper 將
      department query 與 type query 以 `$and` 組合；單一條件時不包 `$and`（決策 5）
- [x] 2.2 `find_nearby_hospitals()` 新增選填參數 `facility_type: str | None = None`
- [x] 2.3 `find_nearby_facilities_by_department()` 新增選填參數 `facility_type: str | None = None`
- [x] 2.4 類型解析失敗時比照科別：不查 DB、回傳可辨識的結果狀態
- [x] 2.5 測試 `tests/unit/services/medical/test_type_filtered_search.py`：
      僅類型過濾、類型×科別疊加（驗證 `$and` 結構）、單一條件不含 `$and`、
      類型解析失敗不查 DB、省略 `facility_type` 時 query 與現狀一致

## 3. 工具層

- [x] 3.1 `app/tools/medical_tools.py`：兩個工具新增 `facility_type` 參數，
      docstring 說明「大醫院／診所／藥局」的使用時機，並註明泛稱「醫院」不應套用（見風險表）
- [x] 3.2 標題與副標反映類型（「附近的醫院」），沿用 `title_override`／`subtitle_override`
- [x] 3.3 藥局查無結果的專屬文案分支
- [x] 3.4 測試：類型標題、藥局專屬文案、類型解析失敗訊息

## 4. 代理層與跨輪保留

- [x] 4.1 `app/services/agent/utils/nodes.py`：新增 `_extract_facility_type_from_history()`，
      沿用 `_DEPARTMENT_INTENT_LOOKBACK` 的回溯上限與跳過座標訊息的邏輯
- [x] 4.2 座標強制注入路徑：科別與類型獨立判斷，兩者可同時帶入
- [x] 4.3 `_is_nearby_facility_intent()`：確認「附近有大醫院嗎」可觸發位置請求
      （「醫院」已在 `_FACILITY_SEARCH_RE` 內，預期已可觸發，須以測試確認）
- [x] 4.4 `app/services/agent/prompt.py`：規則 5(b) 補充 `facility_type` 的使用時機與
      「泛稱醫院不套類型」的界線
- [x] 4.5 測試 `tests/unit/services/agent/test_facility_type_intent.py`：
      僅類型、僅科別、兩者並存、需求過時不沿用

## 5. i18n

- [x] 5.1 `app/i18n/messages.py` 新增六語言：
      `location.type.title`（附近的{type}）、`location.type.unknown`、`location.type.pharmacy_none`

## 6. 驗證

- [x] 6.1 `python -m pytest` 全綠（既有 804 項不得回歸）
- [x] 6.2 對真實資料庫驗證：台北車站「附近有大醫院嗎」SHALL 回傳醫院類而非診所
      （對照 proposal 中記錄的現況：5 筆全為診所）
- [x] 6.3 對真實資料庫驗證疊加：「大醫院的腸胃科」結果須同時滿足兩條件
- [x] 6.4 對真實資料庫驗證藥局：確認走專屬文案而非通用查無訊息

## 7. 規格同步

> 執行紀錄：Task 5（i18n）實際在 Task 3 之前執行 —— Task 3 的 3.2/3.3 需要 `location.type.*` 文案。
> Task 4 經兩輪修正迴圈收斂（具名院所誤判 → 閘門矯枉過正 → 改用緊鄰語法標記判別）。

- [x] 7.1 實作完成後 `openspec validate facility-type-filter` 通過
- [ ] 7.2 merge 後 `openspec archive facility-type-filter`（本 change 的 `specs/` 尚未併入主 spec，
      **不加** `--skip-specs`）
