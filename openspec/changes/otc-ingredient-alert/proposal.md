## Why

長輩自己去藥局買成藥吃，家人不會知道，而**成分重複是台灣最常見的成藥意外**。

問題不在於「吃了成藥」，而在於長輩無從得知不同商品名的藥含有相同成分。實測許可證資料集（2026-09-02，72,037 筆）：

| 非處方藥最常見成分 | 品項數 | 重複的後果 |
|---|---|---|
| CHLORPHENIRAMINE MALEATE | 2,128 | 抗組織胺加倍 → 嗜睡、頭暈 → 跌倒 |
| ACETAMINOPHEN | 1,858 | 超過每日上限 → 肝損傷 |
| DL-METHYLEPHEDRINE HCL | 1,200 | 升血壓 |

非處方藥共 15,191 種，其中 **1,739 種含乙醯胺酚（11.4%）**。關鍵在於它們的外觀完全看不出來：

```
"達德士"安痛錠500毫克      1 種成分   ← 純乙醯胺酚，還算看得出來
"福元" 鼻寧通膠囊          2 種成分   ← 感冒藥，裡面藏了乙醯胺酚
小兒感冒藥顆粒            4 種成分   ← 同上
```

長輩認為「感冒藥」與「止痛藥」是兩種不同的東西，因此可以一起吃。這正是最典型的過量情境。

### 現有的安全警示抓不到這個

`SafetyAlertService` 的判定門檻是「來源可疑」——境外代購、外文字符、藥證庫查無。成藥是合法、藥局買得到、藥證庫查得到的，`assess()` 會判 `catalog_hit → "none"`，不會通報。這不是缺陷：那支服務要解決的是另一個問題。

### 資料已經在手邊，只是沒被取用

`scripts/build_drug_catalog.py` 已經在下載許可證資料集（dataset 9122），該資料集有 28 個欄位，建表腳本只取了 3 個。其中兩欄正是本提案需要的：

- **藥品類別**（第 15 欄）：判斷是不是非處方藥
- **主成分略述**（第 17 欄）：94.4% 有值，以 `;;` 分隔的英文學名

用英文學名比對比中文品名可靠得多——普拿疼、斯斯、明通治痛丹叫不同名字，主成分都是 `ACETAMINOPHEN`。

## What Changes

1. **建表腳本多取兩欄**，並依藥事法第 8 條把 24 種「藥品類別」寫法歸為四級（處方藥／指示藥／成藥／非成品藥）。
2. **新增成分重複偵測**：非處方藥加入用藥提醒時，比對當事人現有用藥的成分集合。
3. **新增推播種類 `otc_medication_added`**，沿用既有的 `NOTIFICATION_POLICY`（GUARDIAN／CAREGIVER），不改動政策表。
4. **當事人本人同時收到提示**——他才是站在藥局門口的那個人。

### 明確不做

- **不阻擋任何操作。** 掃描與加入提醒的流程完全不變，偵測到重複只是多一則訊息。長輩不會知道自己被什麼擋下來，而子女本來就可以事後代為調整。
- **不對全部成分比對。** 維生素 B 群重複沒有臨床意義，報出來只會變成雜訊，淹掉真正該看的那則。只比對明確會累加致害的成分白名單，見 design.md。
- **不推給 MEMBER。** 理由不是權限而是訊息品質，見 design.md 決策 3。
- **不做交互作用判定。** 那需要藥理知識庫與專業責任，超出本系統定位。成分重複是可計算的，交互作用不是。

## Capabilities

### New Capabilities

- （無；擴充既有。）

### Modified Capabilities

- `drug-safety-alert`：新增「成分重複」這一類風險，與既有的「來源可疑」並列。
- `user-roles`：`NotificationKind` 新增 `otc_medication_added`。政策表本身不變。
- `medication-identification`：藥證庫條目新增 `drug_class` 與 `ingredients` 欄位。

## Impact

- **程式**：`scripts/build_drug_catalog.py`、`app/services/medication/drug_catalog_service.py`、`app/services/safety/`、`app/models/family_authorization.py`
- **資料**：`resources/drug_catalog.json` 每筆多兩個欄位（約 +3 MB）。需重跑建表腳本，**不需要重新下載藥丸照片、不需要 LLM**。
- **API/route**：無影響。
- **行為**：未偵測到重複時，非處方藥仍會通知家人（見 design.md 決策 4）；處方藥完全不受影響。
- **測試**：`tests/unit/scripts/test_build_drug_catalog.py`、`tests/unit/services/safety/`、`tests/unit/models/test_family_authorization.py`

## 尚未決定的事

成分白名單的初版內容需要有依據。本提案先以「實測出現頻率最高且已知會累加致害」為準（乙醯胺酚、抗組織胺、偽麻黃鹼類），但那是資料驅動的起點而非藥理審查的結論。tasks 的第 1 節即為建立白名單並記錄每一項的納入理由。
