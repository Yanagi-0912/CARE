## ADDED Requirements

### Requirement: 急迫度判斷獨立於掛號意圖，且短路整條流程

系統 SHALL 在進入 agent 之前執行急迫度判斷。判定為緊急時 SHALL 立即回傳緊急就醫建議並結束流程，SHALL NOT 產生任何門診科別建議，SHALL NOT 呼叫 RAG 或其他工具，亦 SHALL NOT 將急診提示與門診建議並陳。

急迫度判斷 SHALL NOT 以使用者是否詢問科別、是否描述症狀、或任何工具是否被呼叫為前提。此條為本需求的核心：前一版把判斷放在「症狀＋掛號意圖」的分支後面，導致「我阿公昏迷」因未詢問科別而完全跳過檢查。安全檢查 SHALL NOT 實作為 agent 可選擇不呼叫的工具。

急迫度判斷 SHALL 以語意判斷實作，判準為「所述狀況是否正在發生且需要立即處置」，SHALL NOT 以字面關鍵字清單實作。理由：關鍵字清單的語序覆蓋不完全（`車禍` 命中而「被車撞」不命中），且僅適用單一語言，其餘語言等同無安全網。

判斷 SHALL 同時要求「正在發生」與「需要立即處置」兩者成立。僅後者成立者多為知識性問句（「中風要怎麼急救」），SHALL NOT 觸發緊急回覆。

判斷發生例外或逾時時 SHALL 降級為「不緊急」（fail-open）。此處與前一版的 fail-closed 相反：判斷來源改為遠端模型後，失敗來自網路與配額而非程式錯誤，fail-closed 會使每次服務中斷都對所有使用者顯示緊急卡，令該提示失去意義。此代價 SHALL 記錄於風險並以召回率量測約束。

急迫度判斷的範圍 SHALL 限於生理急症。表達自殺、輕生或自傷**意念**時 SHALL 判為不緊急並交由一般流程處理，因為本判斷觸發的回覆內容（前往急診、119、110）不適用於該情境；判對了送錯卡比不判更糟。此排除 SHALL 於判斷層明說，SHALL NOT 僅倚賴移除下游的處置分支。

已發生且正在造成生理危險的自傷行為（如服藥過量、割傷出血不止）SHALL 仍屬本判斷範圍。分界為「意念」與「已造成的生理傷害」。

#### Scenario: 自殺意念不觸發緊急回覆

- **WHEN** 使用者表達「我要燒炭自殺」
- **THEN** 急迫度判斷為不緊急，流程照常進入 agent，SHALL NOT 顯示緊急就醫卡

#### Scenario: 已發生的自傷仍觸發緊急回覆

- **WHEN** 使用者描述「我剛剛吞了一整罐安眠藥」
- **THEN** 急迫度判斷為緊急，回傳緊急就醫建議

#### Scenario: 未詢問科別仍執行判斷

- **WHEN** 使用者描述「我阿公昏迷」而未詢問科別
- **THEN** 系統回傳緊急就醫建議，SHALL NOT 改走 RAG 或科別建議

#### Scenario: 緊急回覆不給門診科別

- **WHEN** 使用者的敘述被判定為緊急
- **THEN** 回覆中 SHALL NOT 出現任何部定專科名稱作為建議

#### Scenario: 知識性問句不觸發緊急回覆

- **WHEN** 使用者詢問「中風前兆有哪些」
- **THEN** 判斷結果為不緊急，流程照常進入 agent

#### Scenario: 判斷失敗時降級為不緊急

- **WHEN** 急迫度判斷拋出例外或逾時
- **THEN** 系統視為不緊急並續行一般流程，且該次失敗 SHALL 留下紀錄

### Requirement: 症狀正規化 SHALL NOT 產生科別

系統 SHALL 將使用者的口語症狀詞正規化為對照表中已存在的症狀條目。正規化 SHALL 先查同義詞表，未命中時始得使用語言模型兜底。

語言模型的輸出 SHALL 以封閉集合約束為「對照表中的症狀條目」或 `UNKNOWN`，其輸出結構中 SHALL NOT 包含科別欄位。科別 SHALL 僅由對照表決定。

正規化失敗（例外、逾時、無法解析）時 SHALL 降級為 `UNKNOWN`，SHALL NOT 中斷流程。

#### Scenario: 口語詞對應到表內條目

- **WHEN** 使用者說「肚子痛」而對照表中的條目為「腹痛」
- **THEN** 正規化結果為「腹痛」，並以該條目執行比對

#### Scenario: 模型無法輸出科別

- **WHEN** 語言模型兜底被呼叫
- **THEN** 其可回傳的值僅限對照表中的症狀條目或 `UNKNOWN`

#### Scenario: 正規化失敗降級

- **WHEN** 正規化拋出例外
- **THEN** 結果為 `UNKNOWN`，流程續行至保底建議

### Requirement: 對照表載入時強制轉為部定專科

對照表載入時，系統 SHALL 對每一條目的科別值呼叫 `resolve_department()` 轉為部定專科。任一條目無法解析時 SHALL 於載入階段拋出錯誤，SHALL NOT 於線上查詢時才略過。

`confidence` 非 `verified` 的條目 SHALL NOT 進入線上查詢。

#### Scenario: 科別無法解析時載入失敗

- **WHEN** 對照表含科別值「15歲以下兒童」且無法解析為部定專科
- **THEN** 載入 SHALL 拋出錯誤，服務 SHALL NOT 以部分載入的表提供服務

#### Scenario: 未審定條目不參與比對

- **WHEN** 條目的 `confidence` 為 `unverified`
- **THEN** 該條目 SHALL NOT 被載入，比對時視同不存在

### Requirement: 建議為多候選並具保底

比對命中時，系統 SHALL 回傳至多 3 個候選科別，且每個候選 SHALL 為部定專科。

下列情形 SHALL 走保底建議——回傳初診方向（家醫科或一般內科）並明確說明系統無法判斷：

- 相似度低於 `SYMPTOM_MATCH_MIN_SCORE`
- 正規化結果為 `UNKNOWN`
- 候選科別超過 3 個

保底情形下 SHALL NOT 退化為採用對照表中最接近的一條。

#### Scenario: 低於門檻走保底

- **WHEN** 最高分候選的相似度低於 `SYMPTOM_MATCH_MIN_SCORE`
- **THEN** 系統回傳保底建議與無法判斷的說明，SHALL NOT 採用該候選的科別

#### Scenario: 候選過多走保底

- **WHEN** 某症狀對應到 4 個以上科別
- **THEN** 系統回傳保底建議，SHALL NOT 列出全部候選

#### Scenario: 建議可銜接科別搜尋

- **WHEN** 系統建議「內科」
- **THEN** 該值 SHALL 為 `find_nearby_facilities_by_department` 可直接使用的部定專科

### Requirement: 建議不主動請求位置

產生科別建議後，系統 SHALL NOT 呼叫 `request_location_quick_reply`。使用者若接續表達找院所意圖，SHALL 由既有的科別意圖跨輪保留機制銜接。

#### Scenario: 只問科別不請位置

- **WHEN** 使用者問「我肚子好痛要掛哪一科」
- **THEN** 系統回傳科別建議，SHALL NOT 請求位置

#### Scenario: 使用者接續找院所

- **WHEN** 使用者在取得建議後說「附近有嗎」
- **THEN** 系統依既有鄰近搜尋流程請求位置，並以歷史中的科別執行搜尋

### Requirement: 功能旗標與預設關閉

系統 SHALL 以 `SYMPTOM_DEPARTMENT_ENABLED` 控制本能力，預設值 SHALL 為 false。為 false 時工具集 SHALL NOT 包含 `suggest_department_for_symptom`，其餘行為 SHALL 與本能力存在前完全相同。

#### Scenario: 旗標關閉時回到原行為

- **WHEN** `SYMPTOM_DEPARTMENT_ENABLED` 為 false 且使用者問「我肚子好痛要掛哪一科」
- **THEN** 代理依既有規則呼叫 `get_rag_answer`，行為與本能力存在前相同
