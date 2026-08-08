## Context

`clinicTime` 覆蓋率 100%（96.9% 有實際時段），結構為：

```python
clinicTime: {
  "monday": {"isClosed": False, "slots": [{"open": "08:00", "close": "12:00"},
                                          {"open": "14:00", "close": "17:30"}]},
  ...
}
```

現行 `_get_business_status()` 位於 `resources/flex_messages/medical_messages/`
（呈現層），回傳 `bool | None`，且僅判斷「當下是否在某個時段內」。

實測數據見 proposal。三個關鍵事實：`clinicTime` 是門診時間而非急診時間；
午休與深夜兩時段營業率極低（11.5% / 0.2%）；`notes` 有 691 家提及休診但 `clinicTime` 不反映。

## Goals / Non-Goals

**Goals**

- 回答「我什麼時候能去」而非只回答「現在有沒有開」。
- 營業狀態的誤判不得造成院所被藏起來，尤其是急診。
- 揭露 `notes`，補上目前完全未使用的資訊。

**Non-Goals**

- 不解析 `notes` 的日期語意。格式極不規則（全形斜線、無分隔、民國年混用），
  寫 parser 的正確率無法保證，而錯誤方向會傷害使用者。只做「有無日期樣式」的粗分類。
- 不推斷急診的實際開放時間。資料未記載，宣稱即為編造。
- 不將營業判斷下推為 MongoDB 查詢條件（見決策 4）。
- 不做院所類型篩選（另案 `facility-type-filter`）。

## Decisions

### 決策 1：採「狀態一律顯示 + 下次開診」，篩選僅在明說時觸發

三個候選策略：

| 策略 | 評估 |
|---|---|
| A. 硬篩選（只回營業中）| ✗ 午休砍 88%、深夜砍 99.8%；急診情境會害人；617 家 notes 誤判會直接藏掉院所 |
| B. 排序（營業中優先）| ✗ 卡片一次只顯示 5 張，排序實質等同篩選，卻又破壞「由近到遠」這個使用者唯一能預期的排列邏輯 |
| C. 標記 + 下次開診（**採用**）| ✓ 不清空、不藏東西；notes 誤判的代價僅是標籤不精準；使用者自行決定 |

A 保留為「使用者明確要求」時的行為，且加上 0 筆退回機制，使其失敗模式不會退化成「查無資料」。

**核心理由**：使用者的問題不是「這家有開嗎」，而是「我什麼時候能去」。
篩選與排序都是在逼近這個問題的替代品；「下次開診時間」是直接回答。

### 決策 2：邏輯移出呈現層，新建 `app/services/medical/business_hours.py`

現行 `_get_business_status()` 錯置於 Flex 訊息模組。狀態邏輯即將從
「一個 bool」擴張為「七種狀態 + 下次開診 + 急診豁免 + notes 規則」，
留在呈現層會使其無法獨立測試，也違反 `backend-architecture` 的分層慣例。

介面設計：

```python
class BusinessStatus(StrEnum):
    OPEN = "open"                 # 營業中
    BREAK = "break"               # 午休中（今日尚有後續時段）
    CLOSED_TODAY = "closed_today" # 今日已結束
    CLOSED_DAY = "closed_day"     # 今日休診
    EMERGENCY = "emergency"       # 設有急診（豁免）
    CALL_AHEAD = "call_ahead"     # 請電洽（長期性 notes）
    UNKNOWN = "unknown"           # 無資料

@dataclass(frozen=True)
class BusinessHoursResult:
    status: BusinessStatus
    next_open: NextOpen | None    # (weekday_key, "08:00", is_today)
    note: str | None              # notes 原文，供呈現層顯示
```

`now` 以參數注入（預設取台灣時間），使測試不需 monkey patch ——
符合 `config.yaml` 的 `tasks` 規則「禁止使用 monkey patch，請使用依賴注入」。

### 決策 3：急診以「負面規則」實作，而非正面宣稱

標籤只講資料講過的事：`departments` 含急診醫學科 → 「設有急診」。
**不**標「24 小時」。

真正的保護是一條負面規則：`open_now` 過濾 SHALL NOT 排除急診院所。
這條規則寫在過濾邏輯裡，而非依賴狀態標籤恰好不是「休診」——
兩者解耦，未來改動狀態文案不會意外破壞安全性。

### 決策 4：營業狀態在應用層計算，不下推 Mongo

`clinicTime` 是「七個 key 各含 slots 陣列」的嵌套結構，要用 `$expr` 在 `$geoNear` 內比對
當日時段，查詢會極其複雜且無法利用索引。

改為 over-fetch：`open_now=True` 時將 `target_count` 放大（× 4，上限 20）取回候選，
在應用層過濾後取前 N 筆。若過濾後不足，沿用既有階梯機制的 `satisfied=False` 語意。

**Trade-off**：多取回約 15 筆文件。以單次查詢的成本衡量可忽略，
且避免了無法索引的 `$expr` 查詢。

### 決策 5：`notes` 以「有無日期樣式」兩層處理，不寫日期 parser

實測 `notes` 樣本：

```
'1／1上午休診1／1下午休診1／1晚上休診'      80 家   ← 含日期，綁定元旦
'春節假期2／17~2／22休診'                        ← 含日期，綁定春節
'如需看診請先電話洽詢'                     47 家   ← 無日期，長期性
'國定假日休診'                              8 家   ← 無日期，長期性
'以提供血液透析服務為主'                            ← 無日期，非休診資訊
```

初版曾考慮將 691 家提及休診者一律降級為「請電洽」，但這是錯的：
其中絕大多數綁定特定日期，八月因元旦註記而永久降級會使標籤失去意義。

採用規則：`re.search(r"\d+\s*[／/]\s*\d+", notes)` 命中即視為日期綁定 → 僅顯示原文。
未命中且含休診關鍵字 → 降級為「請電洽」。

`notes` 原文則**一律顯示**。二月看到「營業中 ※春節假期2／17~2／22休診」使用者自會警覺；
八月看到同一行則明顯無關。不需 parser，也不需猜測，並同時補上 `notes` 從未揭露的缺口。

### 決策 6：狀態優先序

多條規則可能同時成立，判定順序 SHALL 為：

```
1. 設有急診          → EMERGENCY      （最高，安全考量）
2. 長期性 notes      → CALL_AHEAD
3. 無任何時段資料    → UNKNOWN
4. 今日 isClosed     → CLOSED_DAY
5. 當下在時段內      → OPEN
6. 今日尚有後續時段  → BREAK
7. 其他              → CLOSED_TODAY
```

急診置於最前，確保任何 notes 或時段狀況都不會使急診院所顯示為休診。

## Risks / Trade-offs

| 風險 | 影響 | 緩解 |
|---|---|---|
| 「設有急診」被誤解為「現在可看急診」 | 使用者白跑 | 文案僅陳述資料事實；不宣稱時間；急診本質即為緊急才前往 |
| 617 家含日期 notes 仍標「營業中」 | 春節期間可能白跑 | notes 原文同時顯示，使用者可自行判斷；不做全年降級以免標籤失效 |
| 跨週計算下次開診有邊界錯誤 | 顯示錯誤時間 | 以注入的 `now` 測試七天邊界（週日→週一、當日最後時段後） |
| over-fetch 後仍不足目標筆數 | 結果偏少 | 沿用 `satisfied=False` 的部分結果語意，文案說明 |
| 民國年格式（「115／01／01」）| 日期樣式偵測誤判 | 該格式仍含 `／` 數字樣式，會被正確歸為「含日期」，行為符合預期 |

## Migration Plan

無資料庫遷移。`MedicalFacility` 新增選填欄位 `notes`，既有建構呼叫不受影響。
`_get_business_status()` 與 `_build_status_indicator()` 由新模組取代，
兩個 Flex 模組（brief／detail）同步改用；舊函式移除。
`open_now` 為選填參數，省略時搜尋結果與現狀完全一致。

## Open Questions

- 「設有急診」是否應進一步區分醫學中心／區域醫院／地區醫院？
  資料庫的 `type` 僅到「醫院／綜合醫院」層級，無法直接判斷急診能力等級。
- 深夜情境是否應主動將設有急診的院所排到前面（而非僅保留）？
  這會與「距離排序」衝突，暫不實作，待實際對話樣本再評估。
