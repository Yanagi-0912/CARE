# 科別推薦回應時間

腳本：`scripts/medical_bench/symptom_department_bench.py`，直接呼叫正式的 `SymptomDepartmentService`。
報告：`reports/symptom-department-<env-label>-YYYYMMDD-HHMMSS.json`（每一次的時間、實際路徑、建議科別）與同名 `.md`（摘要）。json 只留在本機、不進版控，版控只收 `.md`。

腳本共用 `facility_search_bench.py` 的專案根目錄找法與統計，兩支要放在同一個資料夾。

## 量什麼、數字代表什麼

- 使用者問「XX 要掛哪一科」時，`SymptomDepartmentService.suggest` 從收到說法到給出建議科別（或保底）的時間。
- 正規化層依向量分數走不同路徑，時間差很多，所以分開報：

| 路徑 | 條件 | 呼叫什麼 | 測試句 |
|---|---|---|---|
| 直接命中 | 向量分數 ≥ 0.95 | embedding 1 次 | 頭痛、咳嗽、腹痛（對照表原樣條目） |
| 中間帶 | 0.87～0.95 | embedding 1 次＋LLM 1 次 | 拉肚子拉了三天、吃東西就想吐、常常忘東忘西 |
| 未命中 | < 0.87 | embedding 1 次，給保底 | 今天天氣很好、我想買一支新手機、明天要去哪裡玩 |
| 過長 | 超過 60 字 | 不呼叫 API，給保底 | 一段 62 字的睡不好描述 |

- 「中間帶」的句子取自 `evals/symptom_normalizer/dataset.jsonl` 裡分數 0.90～0.92 的句子。
- 對照表擴充、向量重建後分數會變，所以報告**依實際路徑分組**（由正式程式的 log 判斷），並列出和預期不同的句子。
- embedding 另外計時，LLM 那一段約為總時間減掉 embedding。
- 正規化層會用快取記住比過的說法，所以每一次都重建正規化器，量到的是**沒有快取**的情況。
- **不含**：Gemini agent 判斷要呼叫科別推薦工具的時間、卡片產生、LINE 推播。急迫度判斷擋在更前面，也不在這裡量。

## 費用

**會呼叫 Gemini**，用的是正式環境的 API 金鑰，與使用者共用額度。預設 10 句 ×（1 次暖身＋5 次）：

| 呼叫 | 次數（含暖身） | 每次用量 | 估計 |
|---|---|---|---|
| embedding（`EMBEDDING_MODEL`） | 約 54 | 不到 20 token | 不到 0.001 美元 |
| LLM（`MODEL_NAME`，正式環境 gemini-3.8-flash，思考強度預設 medium） | 約 18（只有中間帶） | 輸入約 350、輸出含思考約 500 token | 約 0.04 美元 |

- 價格依 2026-09-26 查的官方價目表：gemini-3.8-flash 每百萬 token 輸入 0.75、輸出 3.75 美元（2026 年底前）；embedding 以每百萬 token 0.20 美元估計。
- 未命中的句子若落進中間帶，會多叫 LLM。LLM 呼叫達到 `--max-llm-calls`（預設 30）就停止，報告會註明提早停止。

## 在本機跑

需要 `.env` 裡的 `GEMINI_API_KEY`（腳本會自動讀）。本機的數字只能當參考。請用 `.venv`（`venv/` 缺 numpy），不要用 Code Runner 的三角形按鈕執行。

```bash
# PowerShell
.\.venv\Scripts\python.exe scripts\medical_bench\symptom_department_bench.py --env-label local
# Git Bash
.venv/Scripts/python.exe scripts/medical_bench/symptom_department_bench.py --env-label local
```

## 在 GCP 跑（暫時獨立 pod，不碰正式服務）

- **千萬不要在 `care-backend` pod 裡跑**：它只有 1 份，已用約 964Mi，OOM 會讓所有使用者收不到回覆。
- 用 backend 的同一個映像另開暫時 pod `symptom-bench`。它和正式 backend 在同一台 VM、同一個網路。
- 設定只掛需要的值：
  - `GEMINI_API_KEY`：從 `care-backend-secret` 用 `secretKeyRef` 掛；
  - `MODEL_NAME`、`EMBEDDING_MODEL`：從 `care-backend-config`（ConfigMap）掛，確保和正式環境同一個模型。
- 不連資料庫。

1. 本機：把兩支腳本送到 VM 的 `/tmp`，再登入 VM。

```bash
gcloud compute scp scripts/medical_bench/facility_search_bench.py scripts/medical_bench/symptom_department_bench.py care-vm:/tmp/ --zone asia-east1-b --project project-09223ab4-b0ce-4aee-a99
gcloud compute ssh care-vm --zone asia-east1-b --project project-09223ab4-b0ce-4aee-a99
```

2. VM 上一個指令做一件事，照順序執行。
   - 暫時 pod 最多 90 分鐘後自己結束（`sleep 5400`），結束後裡面的報告就消失，所以 2-5 到 2-8 要在 90 分鐘內做完。
   - 刪除 pod（2-10）要自己手動執行。

```bash
# 2-0. 看資源：整台 VM 剩不到約 300 MiB 就先不要跑
sudo kubectl -n care-dev top pod
sudo kubectl top node

# 2-1. 掃描現有的 pod：確認沒有上次殘留的 symptom-bench（有的話先執行 2-10 刪掉）
sudo kubectl -n care-dev get pods

# 2-2. 取得 backend 正在用的映像（換了 SSH 視窗要重新執行）
IMAGE=$(sudo kubectl -n care-dev get deploy care-backend -o jsonpath='{.spec.template.spec.containers[0].image}')
echo "$IMAGE"

# 2-3. 開暫時 pod：上限 256Mi、0.5 核；只掛 GEMINI_API_KEY（secret）與兩個模型名稱（ConfigMap）
sudo kubectl -n care-dev run symptom-bench --image="$IMAGE" --restart=Never \
  --overrides='{"spec":{"containers":[{"name":"symptom-bench","image":"'"$IMAGE"'","command":["sleep","5400"],"env":[{"name":"GEMINI_API_KEY","valueFrom":{"secretKeyRef":{"name":"care-backend-secret","key":"GEMINI_API_KEY"}}},{"name":"MODEL_NAME","valueFrom":{"configMapKeyRef":{"name":"care-backend-config","key":"MODEL_NAME"}}},{"name":"EMBEDDING_MODEL","valueFrom":{"configMapKeyRef":{"name":"care-backend-config","key":"EMBEDDING_MODEL"}}}],"resources":{"limits":{"memory":"256Mi","cpu":"500m"}}}]}}'

# 2-4. 等 pod 準備好（出現 condition met 再往下）
sudo kubectl -n care-dev wait --for=condition=Ready pod/symptom-bench --timeout=120s

# 2-5. 把兩支腳本複製進 pod（腳本改過就要重新 scp 到 VM、再重新 cp）
sudo kubectl -n care-dev cp /tmp/facility_search_bench.py symptom-bench:/tmp/facility_search_bench.py
sudo kubectl -n care-dev cp /tmp/symptom_department_bench.py symptom-bench:/tmp/symptom_department_bench.py

# 2-6. 執行量測（約 1～2 分鐘；embedding 約 54 次、LLM 約 18 次，上限 30 次）
sudo kubectl -n care-dev exec symptom-bench -- sh -c "cd /app && python /tmp/symptom_department_bench.py --env-label gcp-pod --out /tmp/symptom-department-reports"

# 2-7. 清掉 VM 上上一次的報告資料夾
rm -rf /tmp/symptom-department-reports

# 2-8. 把報告從 pod 拷到 VM
sudo kubectl -n care-dev cp symptom-bench:/tmp/symptom-department-reports /tmp/symptom-department-reports

# 2-9. 把報告改成自己擁有，並確認有 .json 與 .md
sudo chown -R "$USER" /tmp/symptom-department-reports
ls -la /tmp/symptom-department-reports

# 2-10. 手動刪除暫時 pod：確認 2-9 看得到報告後再刪
sudo kubectl -n care-dev delete pod symptom-bench

# 2-11. 再掃描一次：symptom-bench 不應該出現，其他 pod 的狀態也應該和 2-1 一樣
sudo kubectl -n care-dev get pods
```

3. 本機：把報告拿回 repo（第一次要先建資料夾）。

```bash
mkdir -p evals/symptom_department/reports
gcloud compute scp "care-vm:/tmp/symptom-department-reports/*" evals/symptom_department/reports/ --zone asia-east1-b --project project-09223ab4-b0ce-4aee-a99
```

注意：

- 腳本靠工作目錄 `/app` 找 `app` 套件，一定要先 `cd /app`。
- 2-3 若出現找不到 secret、ConfigMap 或 key，先只列出名稱來看（不會印出值）：
  - `sudo kubectl -n care-dev get secret care-backend-secret -o jsonpath='{.data}' | tr ',' '\n' | cut -d: -f1`
  - `sudo kubectl -n care-dev get configmap care-backend-config -o jsonpath='{.data}' | tr ',' '\n' | cut -d: -f1`
- 看其他 namespace 有沒有殘留的暫時 pod：`sudo kubectl get pods -A`。
