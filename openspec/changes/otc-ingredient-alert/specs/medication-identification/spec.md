## ADDED Requirements

### Requirement: 藥證庫條目帶藥品分級與主成分

藥證庫的每一筆條目 SHALL 包含 `drug_class`（藥品分級）與 `ingredients`（主成分清單）兩個欄位。

`drug_class` SHALL 為 `prescription`（處方藥）、`otc_guided`（指示藥）、`otc`（成藥）、`not_a_medicine`（製劑原料、空膠囊等非成品藥）之一，或空字串。

分級 SHALL 由許可證資料集「藥品類別」欄的完整值逐一對照而得，SHALL NOT 以關鍵字比對推導。「須經醫師指示使用」含「醫師」二字但依藥事法第 8 條屬指示藥，任何「含醫師即為處方藥」的規則都會將該類別歸錯，而該類別在實測資料中有 5,842 筆。

對照表未涵蓋的值 SHALL 回空字串，SHALL NOT 猜測歸入任一分級——猜成處方藥會少提醒，猜成非處方藥會多打擾，兩個方向都不可接受。

#### Scenario: 指示藥不得被歸為處方藥

- **WHEN** 藥品類別為「須經醫師指示使用」
- **THEN** `drug_class` 為 `otc_guided`

#### Scenario: 非成品藥自成一級

- **WHEN** 藥品類別為「製劑原料」「空膠囊」等
- **THEN** `drug_class` 為 `not_a_medicine`，且該條目 SHALL NOT 參與成分重複偵測

#### Scenario: 未知類別不猜測

- **WHEN** 藥品類別為對照表未涵蓋的新值
- **THEN** `drug_class` 為空字串
