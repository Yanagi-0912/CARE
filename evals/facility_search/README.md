# 醫療院所查詢：回應時間與名稱查詢覆蓋率

兩支腳本，都在 `scripts/medical_bench/`：

| 腳本 | 量什麼 | 報告 |
|---|---|---|
| `facility_search_bench.py` | 找附近院所、依科別找、依名稱找的回應時間 | `reports/facility-search-<env-label>-YYYYMMDD-HHMMSS.json`／`.md` |
| `facility_name_coverage.py` | 用每家院所自己的名稱查得不查得到 | `reports/facility-name-coverage-<env-label>-YYYYMMDD-HHMMSS.json`／`.md` |

報告的 json 只留在本機、不進版控，版控只收 `.md`。

`facility_name_coverage.py` 共用 `facility_search_bench.py` 的專案根目錄找法與 DB 錯誤收集，兩支要放在同一個資料夾。

## 回應時間（facility_search_bench.py）

### 量什麼、數字代表什麼

- 直接呼叫正式的 `MedicalService`，32 個案例各跑 1 次暖身＋5 次：
  - **不限科別**（找附近所有院所）：台北車站、埔里、蘭嶼，各查一般與「現在有營業」；埔里另查「診所」。
  - **依科別**：腸胃科（台北）、牙科（埔里）、放射腫瘤科（埔里，少見科別會放寬範圍）、家醫科＋內科＋不分科（保底卡的按鈕）、大醫院的腸胃科。
  - **依名稱**：臺大醫院（別名；不帶座標、從高雄查）、仁愛診所（同名多家；有帶座標、沒帶座標）、花蓮中正診所（開頭是地名）、不存在的院所。
  - **極端地點**（使用者指定）：海洋大學、金門、澎湖望安、壽卡鐵馬驛站（屏東台東交界）、彭佳嶼、豐濱鄉衛生所、梨山。每個地點查兩次：
    - 不限科別找附近院所；
    - 依科別找「不分科」，也就是沒申報專科的一般西醫診所（在這些地點要找科別時，一律找不分科）。

    彭佳嶼 50 公里內一家都沒有，預期回「查無院所」。各地點的家數寫在腳本的 `EDGE_LOCATIONS` 註解裡。
- 地點依院所密度挑。2026-09-26 查 5／10／20／50 公里內的家數：台北車站 2,680／5,978／7,003／9,395，埔里 85／93／106／4,536，蘭嶼 1／1／1／1。
- 量的是「呼叫 service → 拿回院所清單」，幾乎全是 MongoDB 查詢，反映這台機器到資料庫的網路加上查詢時間。
- **不含**：Gemini agent 判斷要叫哪個工具（使用者等待的大頭）、Flex 卡片、LINE 推播。
- 「現在有營業」的結果隨執行時刻而變，報告記錄執行時間。
- 失敗：正式 repository 查詢出錯時只記 ERROR log、回傳空清單。腳本收集那些 log，所以失敗與逾時分得出來。

### 費用

**不呼叫 Gemini，免費。** 正式程式只在科別、類型本地表認不得時才問 LLM。腳本的 `MedicalService` 不接 LLM，開跑前也會確認每個科別、類型本地都認得，有一個認不得就直接結束。

資料庫只讀，共約 192 次 service 呼叫，一次一個，負擔可忽略。

## 名稱查詢覆蓋率（facility_name_coverage.py）

### 量什麼、數字代表什麼

- 對 `medicalFacilities` 的每一家，用它登記的名稱呼叫正式的 `MedicalService.find_facility_by_name`，看最多 20 筆的結果裡有沒有它自己：
  - **不帶座標**：同名的院所查同一句，每個不重複的名稱只查一次（約 19,400 次），結果套用到同名的每一家。
  - **帶座標**：用該院所自己的座標查，模擬使用者就在附近，每家查一次（約 23,200 次）。
- **覆蓋率＝查得到的家數 ÷ 資料庫總家數**，另列排第一的比例，並依院所類型分開列。報告附上查不到的例子，完整結果在 json。
- 用完整登記名稱查是最好查的情況，數字是**上限**。使用者講簡稱（臺大醫院、長庚）的情況要另外準備案例。
- **分母不含藥局。** 藥局在 `medical_facilities_pharmacy`（2026-09-26 有 7,970 家），名稱查詢目前不查那裡。算進分母只會反映「功能還沒做」，把名稱比對本身的漏失蓋掉。報告另列藥局家數。
- 同名：2026-09-26 有 2,223 個名稱不只一家在用，共 6,050 家。最多的是林牙醫診所 14 家、陽明牙醫診所 13 家。名稱查詢是「包含」比對，「林牙醫」也會比對到其他名稱裡有這三個字的診所。所以同名或名稱相近的院所，可能被擠出 20 筆之外，覆蓋率量的就是這件事。

### 費用與對資料庫的負擔

**不呼叫 Gemini，免費。**

資料庫只讀，但每次查詢都是名稱 regex，會掃過整個 collection。全部約 42,600 次：
- 本機實測一次約 0.045 秒；`--concurrency 2`（預設）時約 15～20 分鐘。
- 要更快可以調高 `--concurrency`，但同時掃 collection 的查詢變多，會跟使用者搶資料庫。
- 要更溫和可以加 `--interval`，或用 `--limit` 抽樣。

建議：
1. 先用 `--limit 200` 試跑（約 400 次查詢，幾秒鐘），輸出到 `scratch/`（不進版控）。
2. 再挑離峰時段跑完整版。

完整版的 json 約數 MB。

## 在本機跑

需要 `.env` 裡的 `MONGODB_URI`、`MONGODB_DB`（腳本會自動讀）。本機的數字只能當參考。請用 `.venv`（`venv/` 缺 numpy），不要用 Code Runner 的三角形按鈕執行。

```bash
# PowerShell
.\.venv\Scripts\python.exe scripts\medical_bench\facility_search_bench.py --env-label local
.\.venv\Scripts\python.exe scripts\medical_bench\facility_name_coverage.py --env-label local --limit 200 --out evals\facility_search\scratch
# Git Bash
.venv/Scripts/python.exe scripts/medical_bench/facility_search_bench.py --env-label local
.venv/Scripts/python.exe scripts/medical_bench/facility_name_coverage.py --env-label local --limit 200 --out evals/facility_search/scratch
```

覆蓋率的結果和在哪裡跑無關，只有耗時不同，完整版在本機跑也可以。回應時間的正式數據要在 GCP 跑。

## 在 GCP 跑（暫時獨立 pod，不碰正式服務）

- **千萬不要在 `care-backend` pod 裡跑**：它只有 1 份，已用約 964Mi，OOM 會讓所有使用者收不到回覆。
- 用 backend 的同一個映像另開暫時 pod `facility-bench`。它和正式 backend 在同一台 VM、同一個網路，連同一個資料庫。
- 設定只掛需要的值：
  - `MONGODB_URI`：從 `care-backend-secret` 用 `secretKeyRef` 掛；
  - `MONGODB_DB`：從 `care-backend-config`（ConfigMap）掛。
- 任何地方都不要印出連線字串。

1. 本機：把兩支腳本送到 VM 的 `/tmp`，再登入 VM。

```bash
gcloud compute scp scripts/medical_bench/facility_search_bench.py scripts/medical_bench/facility_name_coverage.py care-vm:/tmp/ --zone asia-east1-b --project project-09223ab4-b0ce-4aee-a99
gcloud compute ssh care-vm --zone asia-east1-b --project project-09223ab4-b0ce-4aee-a99
```

2. VM 上一個指令做一件事，照順序執行。
   - 暫時 pod 最多 90 分鐘後自己結束（`sleep 5400`），結束後裡面的報告就消失，所以 2-5 到 2-10 要在 90 分鐘內做完。
   - 刪除 pod（2-12）要自己手動執行。

```bash
# 2-0. 看資源：整台 VM 剩不到約 300 MiB 就先不要跑
sudo kubectl -n care-dev top pod
sudo kubectl top node

# 2-1. 掃描現有的 pod：確認沒有上次殘留的 facility-bench（有的話先執行 2-12 刪掉）
sudo kubectl -n care-dev get pods

# 2-2. 取得 backend 正在用的映像（換了 SSH 視窗要重新執行）
IMAGE=$(sudo kubectl -n care-dev get deploy care-backend -o jsonpath='{.spec.template.spec.containers[0].image}')
echo "$IMAGE"

# 2-3. 開暫時 pod：上限 256Mi、0.5 核；只掛 MONGODB_URI（secret）與 MONGODB_DB（ConfigMap）
sudo kubectl -n care-dev run facility-bench --image="$IMAGE" --restart=Never \
  --overrides='{"spec":{"containers":[{"name":"facility-bench","image":"'"$IMAGE"'","command":["sleep","5400"],"env":[{"name":"MONGODB_URI","valueFrom":{"secretKeyRef":{"name":"care-backend-secret","key":"MONGODB_URI"}}},{"name":"MONGODB_DB","valueFrom":{"configMapKeyRef":{"name":"care-backend-config","key":"MONGODB_DB"}}}],"resources":{"limits":{"memory":"256Mi","cpu":"500m"}}}]}}'

# 2-4. 等 pod 準備好（出現 condition met 再往下）
sudo kubectl -n care-dev wait --for=condition=Ready pod/facility-bench --timeout=120s

# 2-5. 把兩支腳本複製進 pod（腳本改過就要重新 scp 到 VM、再重新 cp）
sudo kubectl -n care-dev cp /tmp/facility_search_bench.py facility-bench:/tmp/facility_search_bench.py
sudo kubectl -n care-dev cp /tmp/facility_name_coverage.py facility-bench:/tmp/facility_name_coverage.py

# 2-6. 執行回應時間量測（約 1 分鐘，唯讀、不呼叫 Gemini）
sudo kubectl -n care-dev exec facility-bench -- sh -c "cd /app && python /tmp/facility_search_bench.py --env-label gcp-pod --out /tmp/facility-search-reports"

# 2-7. （要跑覆蓋率時）先試跑 200 家，確認沒有失敗、速度正常
sudo kubectl -n care-dev exec facility-bench -- sh -c "cd /app && python /tmp/facility_name_coverage.py --env-label gcp-pod --limit 200 --out /tmp/facility-scratch"

# 2-8. （要跑覆蓋率時）完整版，約 15～20 分鐘，唯讀、不呼叫 Gemini
sudo kubectl -n care-dev exec facility-bench -- sh -c "cd /app && python /tmp/facility_name_coverage.py --env-label gcp-pod --out /tmp/facility-search-reports"

# 2-9. 清掉 VM 上上一次的報告資料夾
rm -rf /tmp/facility-search-reports

# 2-10. 把報告從 pod 拷到 VM
sudo kubectl -n care-dev cp facility-bench:/tmp/facility-search-reports /tmp/facility-search-reports

# 2-11. 把報告改成自己擁有，並確認有 .json 與 .md
sudo chown -R "$USER" /tmp/facility-search-reports
ls -la /tmp/facility-search-reports

# 2-12. 手動刪除暫時 pod：確認 2-11 看得到報告後再刪
sudo kubectl -n care-dev delete pod facility-bench

# 2-13. 再掃描一次：facility-bench 不應該出現，其他 pod 的狀態也應該和 2-1 一樣
sudo kubectl -n care-dev get pods
```

3. 本機：把報告拿回 repo（第一次要先建資料夾）。

```bash
mkdir -p evals/facility_search/reports
gcloud compute scp "care-vm:/tmp/facility-search-reports/*" evals/facility_search/reports/ --zone asia-east1-b --project project-09223ab4-b0ce-4aee-a99
```

注意：

- 腳本靠工作目錄 `/app` 找 `app` 套件，一定要先 `cd /app`。
- 2-3 若出現找不到 secret、ConfigMap 或 key，先只列出名稱來看（不會印出值）：
  - `sudo kubectl -n care-dev get secret care-backend-secret -o jsonpath='{.data}' | tr ',' '\n' | cut -d: -f1`
  - `sudo kubectl -n care-dev get configmap care-backend-config -o jsonpath='{.data}' | tr ',' '\n' | cut -d: -f1`
- 看其他 namespace 有沒有殘留的暫時 pod：`sudo kubectl get pods -A`。
