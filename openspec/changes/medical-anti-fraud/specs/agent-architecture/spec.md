## MODIFIED Requirements

### Requirement: Guardrail 決定是否啟用 RAG

系統 SHALL 在 `guardrail` 節點以注入的「文字→bool」分類器判斷使用者訊息是否與健康醫療或醫療場景識詐相關，並據此設定 `allow_rag`。Guardrail SHALL 不綁定特定模型實作（透過 DI 注入分類器）。當使用者訊息為位置座標訊息時 SHALL 快速跳過分類並禁用 RAG。當分類器發生例外時 SHALL 採 fail-open（視為允許）。分類範圍 SHALL 至少涵蓋：健康、醫療、疾病、藥物、營養、運動、心理健康，以及醫療詐騙／假藥／假醫師／假醫院或健保相關可疑訊息、要求因「醫療／檢驗／健保／保險理賠」而匯款或點擊不明連結等情境。

#### Scenario: 健康相關訊息

- **WHEN** 使用者訊息與健康、醫療、疾病、藥物、營養、運動或心理健康相關
- **THEN** `allow_rag` 設為 `True`，代理可使用 `get_rag_answer` 工具

#### Scenario: 醫療詐騙相關訊息

- **WHEN** 使用者訊息涉及假藥、假醫師、假醫院簡訊、保證療效保健品話術，或因醫療／健保名義要求匯款或點連結
- **THEN** `allow_rag` 設為 `True`，代理可使用 `get_rag_answer` 工具

#### Scenario: 位置座標訊息跳過 RAG

- **WHEN** 使用者訊息以「這是我的目前位置」開頭或包含 `lat=`
- **THEN** 直接禁用 RAG（`allow_rag = False`），不呼叫分類器

#### Scenario: 分類失敗採 fail-open

- **WHEN** 分類器呼叫發生例外
- **THEN** 記錄錯誤並回傳允許（`True`），避免暫時性錯誤阻斷使用者流程

## ADDED Requirements

### Requirement: 醫療識詐與健康查詢必須優先使用 RAG

當本輪工具集已包含 `get_rag_answer`，且使用者問題屬於健康衛教（症狀、疾病、用藥、保健等）或醫療場景識詐查證時，代理 SHALL 先呼叫 `get_rag_answer` 再依工具結果回答，SHALL NOT 僅依模型自身知識逕行給出衛教建議或識詐結論。純寒暄或與健康／醫療識詐無關的短句可不呼叫該工具。系統提示（`SYSTEM_PROMPT`）SHALL 載明上述規則，並說明代理可協助辨識可疑醫療訊息，但不是執法人員、不代替報案；遇急著匯款或點不明連結時 SHALL 強烈勸阻並提示可向官方管道（例如 165 反詐騙諮詢專線）查證。

#### Scenario: 症狀問題先查 RAG

- **WHEN** `allow_rag` 為 `True` 且使用者詢問症狀或衛教建議
- **THEN** 代理呼叫 `get_rag_answer` 後再回答

#### Scenario: 疑似醫療詐騙先查 RAG

- **WHEN** `allow_rag` 為 `True` 且使用者詢問某則醫療相關訊息是否為詐騙／假藥
- **THEN** 代理呼叫 `get_rag_answer` 後再回答，並在適當時提示官方查證管道

#### Scenario: 寒暄可不查 RAG

- **WHEN** 使用者僅寒暄且與健康或醫療識詐無關
- **THEN** 代理可不呼叫 `get_rag_answer`
