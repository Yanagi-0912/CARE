## Why

鄰近搜尋目前無法區分院所規模，而資料分佈極度不均：

```
診所類  18,935 家 (97.0%)      醫院類  464 家 (2.4%)      藥局  116 家 (0.6%)
```

結果是**搜尋結果永遠被診所淹沒**。實測台北車站「附近有醫院嗎」，回傳 5 筆全是診所
（諾貝爾眼科、大道中醫、謝明吉牙醫、立安預防醫學、頌華中醫），一家醫院都沒有。

但「我要找大醫院」與「找間診所看一下」是完全不同的需求 —— 前者可能是急重症、需要住院或檢查，
後者是小病。目前使用者無法表達這個差異，系統也無法回應。

`app/services/medical/medical_facility_matcher.py` 已有 `detect_type_keyword()`，
但只接在**名稱查詢**路徑上，鄰近搜尋完全沒用到。

順帶暴露一個對外承諾與資料不符的問題：`prompt.py` 與 rich menu 都宣稱可以找藥局，
但資料庫僅有 116 家藥局（全台健保特約藥局實際有數千家）。使用者問「附近有藥局嗎」
幾乎必然查無資料。

## What Changes

- 新增 `app/services/medical/facility_type_matcher.py`：使用者語彙 → `type` 欄位值的分類表。
  分為 **醫院／診所／藥局** 三類；刻意**不含牙醫與中醫**，因為這兩個詞已屬科別維度
  （見 design.md 決策 2）。
- `find_nearby_hospitals` 與 `find_nearby_facilities_by_department` 各新增選填參數
  `facility_type`。**不新增第三個工具** —— 類型是過濾維度而非搜尋模式，
  新增工具會讓模型在三個重疊工具間誤選，且無法表達「大醫院的腸胃科」。
- 科別與類型兩個過濾條件 SHALL 可同時成立（`$and` 併入 `$geoNear` 的 `query`）。
  「附近的牙醫診所」＝ 科別牙科 × 類型診所。
- 類型比對使用 `$in` 精確比對（`type` 僅 17 個乾淨值，無髒資料），
  與科別的 regex 策略不同，原因見 design.md 決策 3。
- 回覆標題與副標反映使用者要求的類型（「附近的醫院」而非「附近醫療院所」）。
- 解析不出類型時比照科別的既有原則：明確告知，**不靜默退化為搜尋全部**。
- **藥局資料缺口**：本 change 不補資料，但 SHALL 在查無藥局時給出可行動的訊息
  （建議改問特定藥局名稱或就近院所），而非讓使用者以為系統壞了。

## Capabilities

### New Capabilities

- （無）

### Modified Capabilities

- `location-search`：新增依院所類型篩選、類型與科別可疊加、類型解析失敗處理等需求。
- `agent-architecture`：`find_nearby_hospitals` 與
  `find_nearby_facilities_by_department` 的參數新增 `facility_type`；
  座標強制注入路徑須一併帶入類型意圖。

## Impact

- **新增程式**：`app/services/medical/facility_type_matcher.py`
- **修改程式**：`app/services/medical/medical_service.py`（`_search_tiered` 的 query 組合）、
  `app/tools/medical_tools.py`、`app/services/agent/utils/nodes.py`（類型意圖擷取與跨輪保留）、
  `app/services/agent/prompt.py`、`app/i18n/messages.py`
- **API/route**：無新增或變更 route。行為變更僅在 LINE webhook（`/line/callback`）的對話層。
- **資料庫**：無 schema 變更。`type` 過濾併入既有 `$geoNear` 的 `query`，
  仍由 `location_2dsphere` 索引服務，**不新增索引**。
- **相容性**：`facility_type` 為選填，省略時行為與現狀完全一致，既有測試不需改。
- **測試計畫**：
  - `tests/unit/services/medical/test_facility_type_matcher.py`：分類表解析、別名、
    分類目標值必須存在於資料庫的守門測試
  - `tests/unit/services/medical/test_type_filtered_search.py`：類型過濾、類型×科別疊加、
    類型解析失敗不查 DB
  - `tests/unit/services/agent/test_facility_type_intent.py`：類型意圖擷取、跨輪保留
  - 既有 804 項須維持全綠
