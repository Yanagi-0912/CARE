## 1. 核心：營業狀態與下次開診

- [x] 1.1 `app/schemas.py`：`MedicalFacility` 新增選填欄位 `notes: Optional[str]`
- [x] 1.2 `app/repositories/medical_facility_repository.py`：`_facility_from_doc` 映射 `notes`
- [x] 1.3 新增 `app/services/medical/business_hours.py`：`BusinessStatus`、`NextOpen`、
      `BusinessHoursResult`、`resolve_business_hours(facility, now=None)`；
      `now` 以參數注入（禁 monkey patch）
- [x] 1.4 `next_open` 計算：當日稍後時段 → 跨日 → 跨週（最多七天），查無則回 `None`
- [x] 1.5 狀態優先序依 design.md 決策 6 實作
- [x] 1.6 測試 `tests/unit/services/medical/test_business_hours.py`：
      營業中／午休中／今日已結束／今日休診／無資料、
      下次開診的當日／跨日／跨週／七天皆無、狀態優先序
- [x] 1.7 新增 `BEFORE_OPEN`（今日尚未開診）狀態：真實資料驗證發現深夜 03:00 有 94.3%
      被誤標為「午休中」，因原本只判斷「今日尚有後續時段」，未區分「在時段之間」
      與「在第一個時段之前」

## 2. 急診豁免與負面規則

- [x] 2.1 `resolve_business_hours`：`departments` 含急診醫學科 → `EMERGENCY`，優先於所有其他判定
- [x] 2.2 文案不得含「24 小時」等未經資料證實的營業時間描述（測試斷言）
- [x] 2.3 測試：深夜 03:00 的急診醫院 SHALL 為 `EMERGENCY` 而非 `CLOSED_TODAY`

## 3. notes 兩層規則

- [x] 3.1 日期樣式偵測 `re.search(r"\d+\s*[／/]\s*\d+", notes)`
- [x] 3.2 含日期 → 僅回傳 `note` 原文，不變更狀態；
      不含日期且含休診關鍵字 → `CALL_AHEAD`
- [x] 3.3 測試：「春節假期2／17~2／22休診」維持營業中且回傳原文、
      「如需看診請先電話洽詢」降級為 `CALL_AHEAD`、民國年「115／01／01」歸為含日期

## 4. 呈現層改用新模組

- [x] 4.1 `resources/flex_messages/medical_messages/facility_brief_flex_message.py`：
      移除 `_get_business_status`，改用 `resolve_business_hours`；
      `_build_status_indicator` 改吃 `BusinessHoursResult`，顯示狀態＋下次開診
- [x] 4.2 `facility_detail_flex_message.py` 同步改用；並顯示 `notes` 原文區塊
- [x] 4.3 `resources/flex_messages/theme.py`：新增午休／請電洽／急診的狀態色
- [x] 4.4 既有 `test_facility_brief_flexmessage.py`、`test_facility_detail_flexmessage.py`
      原樣通過（未斷言狀態文字），不需修改
- [x] 4.5 新增 `tests/unit/services/medical/test_status_indicator.py` 覆蓋新的狀態渲染：
      下次開診文字、註記顯示、急診不顯示下次開診

## 5. 營業中篩選與 0 筆退回

- [x] 5.1 `app/services/medical/medical_service.py`：`_search_tiered` 支援 `open_now`，
      以 over-fetch（`target_count × 4`，上限 20）後於應用層過濾（design.md 決策 4）
- [x] 5.2 過濾 SHALL NOT 排除 `EMERGENCY` 狀態的院所（negative rule，獨立於狀態文案）
- [x] 5.3 過濾後 0 筆 → 退回未過濾結果並標記，供工具層產生「目前均未開診」文案
- [x] 5.4 `app/tools/medical_tools.py`：兩個搜尋工具新增選填 `open_now: bool = False`
- [x] 5.5 測試 `tests/unit/services/medical/test_open_now_filter.py`：
      過濾生效、急診不被排除、0 筆退回、省略 `open_now` 時與現狀一致

## 6. 意圖偵測

- [x] 6.1 `app/services/agent/utils/nodes.py`：新增「現在有開／還在看診／現在營業」意圖偵測，
      並比照科別／類型做跨輪保留
- [x] 6.2 `app/services/agent/prompt.py`：規則 5(b) 補充 `open_now` 的觸發條件，
      明確界線為「使用者明說」，泛稱「附近有診所嗎」不觸發
- [x] 6.3 測試 `tests/unit/services/agent/test_open_now_intent.py`

## 7. i18n

- [x] 7.1 `app/i18n/messages.py` 新增六語言：
      `flex.status.break`、`flex.status.closed_today`、`flex.status.closed_day`、
      `flex.status.emergency`、`flex.status.call_ahead`、
      `flex.status.next_open`（{day} {time} 開診）、`flex.facility.note`、
      `location.open_now.none`（目前均未開診，以下為最近院所及下次開診時間）
- [x] 7.2 檢查既有 `flex.status.open` / `flex.status.closed` / `flex.status.unknown` 是否沿用

## 8. 驗證

- [x] 8.1 `python -m pytest` 全綠（既有 804 項不得回歸）
- [x] 8.2 對真實資料庫驗證：以固定 `now` 掃過午休 13:00、深夜 03:00、週日 10:00 三個時段，
      確認狀態分佈與 proposal 記錄的比例一致
- [x] 8.3 對真實資料庫驗證急診：197 家設有急診者於深夜皆為 `EMERGENCY`，
      且 `open_now=True` 時不被排除
- [x] 8.4 對真實資料庫驗證 notes：抽樣確認含日期者未降級、長期性者已降級

## 9. 規格同步

- [x] 9.1 `openspec validate open-now-status` 通過
- [ ] 9.2 merge 後 `openspec archive open-now-status`（不加 `--skip-specs`）
