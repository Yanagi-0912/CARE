# TTS 回應時間量測

腳本：`scripts/speech_bench/chinese_tts_bench.py`（國語，正式程式的 edge-tts 引擎）。
報告：`reports/chinese-tts-<env-label>-YYYYMMDD-HHMMSS.json`（每一次的原始數據）與同名 `.md`（摘要）。json 只留在本機、不進版控，版控只收 `.md`。

台語見文末〈[台語 TTS](#台語-tts)〉：`scripts/speech_bench/taigi_tts_bench.py`，報告 `reports/taigi-tts-*`。

## 數字代表什麼

edge-tts 在微軟的伺服器上合成，執行腳本的機器只負責送文字、收 mp3，所以量到的是
「從這台機器的網路呼叫 edge-tts 要多久」，幾乎與本機運算能力無關：

| 在哪跑 | `--env-label` | 代表 |
| --- | --- | --- |
| 自己的電腦 | `local` | 家用網路到微軟的延遲，只能當參考 |
| GCP VM 上的 backend pod | `gcp-pod` | 正式環境實際的合成延遲 |

兩者都不含存檔、產生音檔網址與 LINE 推播；使用者實際等待的時間比報告長。

## 在本機跑

```bash
.venv/Scripts/python.exe scripts/speech_bench/chinese_tts_bench.py --env-label local
```

## 在 GCP 跑（暫時獨立 pod，不碰正式服務）

**千萬不要在 `care-backend` pod 裡跑。** 它只有 1 份，正在服務使用者（2026-09-25 實測用了
964Mi）；在裡面跑等於和使用者共用同一個容器的記憶體與 CPU 上限，這支腳本光載入程式就要
約 100 MiB，碰到上限 backend 會被 OOMKilled 重啟，重啟期間所有使用者都收不到回覆。

改用與 backend **同一個映像**開一個暫時 pod：同一台 VM、同一個網路，量到的數字一樣代表
正式環境，但有自己的資源上限，跑完就刪，不碰 backend、資料庫、Redis 與 LINE。暫時 pod
不帶 backend 的環境變數與密鑰（edge-tts 不需要）。

CARE 部署在 GCP 專案 `project-09223ab4-b0ce-4aee-a99` 的 VM `care-vm`（`asia-east1-b`）上的 K3s，namespace `care-dev`（設定在 CARE-infra
repo）。K3s 的 API 只在 VM 本機，`kubectl` 要登入 VM 後才能下，而且要加 `sudo`（不加會讀不到 /etc/rancher/k3s/k3s.yaml）。

```bash
# 1. 本機：把腳本送到 VM 的 /tmp（不是家目錄；只是複製檔案，不影響服務）
gcloud compute scp scripts/speech_bench/chinese_tts_bench.py care-vm:/tmp/ --zone asia-east1-b --project project-09223ab4-b0ce-4aee-a99

# 2. 登入 VM
gcloud compute ssh care-vm --zone asia-east1-b --project project-09223ab4-b0ce-4aee-a99

# 3. VM 上：先看資源。整台 VM 剩餘記憶體不到約 300 MiB 就先不要跑
sudo kubectl -n care-dev top pod
sudo kubectl top node
```

4. VM 上：一個指令做一件事，照順序執行。暫時 pod 上限 256Mi、0.5 核，`sleep 5400` 讓它**最多 90 分鐘**
   後自己結束；結束後 pod 裡的報告就跟著消失，已結束的 pod 也無法再 `cp`，所以 4-3 到 4-8 要在
   90 分鐘內做完。刪除 pod（4-10）由自己手動執行，確認報告拷出來了再刪。

```bash
# 4-1. 掃描現有的 pod：確認沒有上次殘留的 tts-bench（有的話先執行 4-10 刪掉）
sudo kubectl -n care-dev get pods

# 4-2. 取得 backend 正在用的映像（換了 SSH 視窗要重新執行，變數不會保留）
IMAGE=$(sudo kubectl -n care-dev get deploy care-backend -o jsonpath='{.spec.template.spec.containers[0].image}')
echo "$IMAGE"

# 4-3. 用同一個映像開暫時 pod
sudo kubectl -n care-dev run tts-bench --image="$IMAGE" --restart=Never \
  --overrides='{"spec":{"containers":[{"name":"tts-bench","image":"'"$IMAGE"'","command":["sleep","5400"],"resources":{"limits":{"memory":"256Mi","cpu":"500m"}}}]}}'

# 4-4. 等 pod 準備好（出現 condition met 再往下）
sudo kubectl -n care-dev wait --for=condition=Ready pod/tts-bench --timeout=120s

# 4-5. 把腳本複製進 pod
sudo kubectl -n care-dev cp /tmp/chinese_tts_bench.py tts-bench:/tmp/chinese_tts_bench.py

# 4-6. 在 pod 裡執行量測（約 1 分鐘，畫面會印出每次秒數與摘要表格）
sudo kubectl -n care-dev exec tts-bench -- sh -c "cd /app && python /tmp/chinese_tts_bench.py --env-label gcp-pod --out /tmp/tts-reports"

# 4-7. 清掉 VM 上上一次的報告資料夾（避免拷出來時混在一起）
rm -rf /tmp/tts-reports

# 4-8. 把報告從 pod 拷到 VM 的 /tmp/tts-reports
sudo kubectl -n care-dev cp tts-bench:/tmp/tts-reports /tmp/tts-reports

# 4-9. 把報告改成自己擁有（sudo 拷出來的是 root 的，不改第 5 步拿不到），並確認有 .json 與 .md
sudo chown -R "$USER" /tmp/tts-reports
ls -la /tmp/tts-reports

# 4-10. 手動刪除暫時 pod：確認 4-9 看得到報告後再刪
sudo kubectl -n care-dev delete pod tts-bench

# 4-11. 再掃描一次：tts-bench 不應該出現，其他 pod 的狀態也應該和 4-1 一樣
sudo kubectl -n care-dev get pods
```

5. 本機：把報告拿回 repo。pscp 不會自動建立本機資料夾，第一次要先建 `reports/`：

```bash
mkdir -p evals/tts/reports
gcloud compute scp "care-vm:/tmp/tts-reports/*" evals/tts/reports/ --zone asia-east1-b --project project-09223ab4-b0ce-4aee-a99
```

注意：

- 腳本靠工作目錄 `/app` 找 `app` 套件，一定要先 `cd /app`。
- 暫時 pod 結束或刪除後，裡面的 `/tmp` 就消失；一定要先做完 4-8、4-9 再做 4-10。
- 忘了刪 pod 也不會一直占著資源：最多 90 分鐘後它會自己結束（狀態變成 `Completed`），但仍會留在
  清單裡，之後還是要執行 4-10 把它刪掉。
- 看其他 namespace 有沒有殘留的暫時 pod：`sudo kubectl get pods -A`。
- 映像已經在這台 VM 上，不需要重新下載。

## 台語 TTS

腳本：`scripts/speech_bench/taigi_tts_bench.py`（正式程式的 `TaigiClient` 與轉檔設定）。
報告：`reports/taigi-tts-<env-label>-YYYYMMDD-HHMMSS.json` 與同名 `.md`。

### 量什麼、怎麼量

正式程式念台語語音回覆分三步（`tts_service._synthesize_taiwanese_or_none`）：Gemini 把華語改寫成台語漢字 →
Taigi TTS 念成 WAV → 轉成 mp3。這支腳本量後兩步，分開計時：

| 欄位 | 量的是什麼 | 受什麼影響 |
| --- | --- | --- |
| 中位數／平均／最快／最慢 | 送出台語漢字 → 收到完整 WAV | 網路到廠商＋廠商合成時間 |
| 轉檔 | WAV → 16 kHz、48 kbps mp3 | 這台機器的 CPU（暫時 pod 限 0.5 核） |
| 合計 | 兩者相加 | |

- 句子是國語五句的**台語漢字版**，含標點剛好 10／25／50／100／150 字，寫死在腳本裡。正式程式不會把華語
  直接交給 Taigi（廠商文件：華語句子可能導致發音錯誤），所以這裡也不送華語。
- **不含** Gemini 改寫：使用者實際還要先等它（2026-09-15～09-22 正式環境中位數約 1.8 秒，當時是
  3.8-flash，現在改 flash-lite）。也不含存檔、產生網址與 LINE 推播。
- 只量時間，不量發音對不對。

### 費用與速率

**會呼叫 Taigi API，不呼叫 Gemini。** 預設每種長度 1 次暖身＋5 次正式，共 30 次，用的是正式環境的金鑰，
與使用者共用額度與速率限制。2026-09-19 實測 Taigi STT 連續打到第 11 次起回 429、每 5 秒一次則全過，
所以每次呼叫之間預設等 6 秒（`--interval`），整輪約 3～4 分鐘。建議避開使用者多的時段。

### 在本機跑

需要 `.env` 裡的 `TAIGI_API_KEY`（腳本會自動讀）。

```bash
# PowerShell
.\.venv\Scripts\python.exe scripts\speech_bench\taigi_tts_bench.py --env-label local
# Git Bash
.venv/Scripts/python.exe scripts/speech_bench/taigi_tts_bench.py --env-label local
```

### 在 GCP 跑（暫時獨立 pod，不碰正式服務）

原則同上面國語的做法：**千萬不要在 `care-backend` 或 `care-tts` pod 裡跑**，用 backend 的映像開暫時 pod，
跑完手動刪掉。和國語不同的兩點：

1. 兩支腳本都要複製進 pod（台語腳本共用國語腳本裡的專案根目錄找法與統計）。
2. 開 pod 時只從 `care-backend-secret` 掛 **`TAIGI_API_KEY` 這一個值**，不帶整份密鑰。

```bash
# 1. 本機：把兩支腳本送到 VM 的 /tmp
gcloud compute scp scripts/speech_bench/chinese_tts_bench.py scripts/speech_bench/taigi_tts_bench.py care-vm:/tmp/ --zone asia-east1-b --project project-09223ab4-b0ce-4aee-a99

# 2. 登入 VM
gcloud compute ssh care-vm --zone asia-east1-b --project project-09223ab4-b0ce-4aee-a99

# 3. VM 上：先看資源。整台 VM 剩餘記憶體不到約 300 MiB 就先不要跑
sudo kubectl -n care-dev top pod
sudo kubectl top node
```

4. VM 上：一個指令做一件事，照順序執行。暫時 pod 最多 90 分鐘後自己結束（`sleep 5400`），4-3 到 4-9 要在
   90 分鐘內做完；刪除 pod（4-10）自己手動執行。

```bash
# 4-1. 掃描現有的 pod：確認沒有上次殘留的 taigi-tts-bench（有的話先執行 4-10 刪掉）
sudo kubectl -n care-dev get pods

# 4-2. 取得 backend 正在用的映像（換了 SSH 視窗要重新執行）
IMAGE=$(sudo kubectl -n care-dev get deploy care-backend -o jsonpath='{.spec.template.spec.containers[0].image}')
echo "$IMAGE"

# 4-3. 開暫時 pod：上限 256Mi、0.5 核，只掛 TAIGI_API_KEY 一個密鑰值
sudo kubectl -n care-dev run taigi-tts-bench --image="$IMAGE" --restart=Never \
  --overrides='{"spec":{"containers":[{"name":"taigi-tts-bench","image":"'"$IMAGE"'","command":["sleep","5400"],"env":[{"name":"TAIGI_API_KEY","valueFrom":{"secretKeyRef":{"name":"care-backend-secret","key":"TAIGI_API_KEY"}}}],"resources":{"limits":{"memory":"256Mi","cpu":"500m"}}}]}}'

# 4-4. 等 pod 準備好（出現 condition met 再往下）
sudo kubectl -n care-dev wait --for=condition=Ready pod/taigi-tts-bench --timeout=120s

# 4-5. 把兩支腳本複製進 pod（腳本改過就要重新複製）
sudo kubectl -n care-dev cp /tmp/chinese_tts_bench.py taigi-tts-bench:/tmp/chinese_tts_bench.py
sudo kubectl -n care-dev cp /tmp/taigi_tts_bench.py taigi-tts-bench:/tmp/taigi_tts_bench.py

# 4-6. 在 pod 裡執行量測（約 3～4 分鐘，會呼叫 Taigi 30 次）
sudo kubectl -n care-dev exec taigi-tts-bench -- sh -c "cd /app && python /tmp/taigi_tts_bench.py --env-label gcp-pod --out /tmp/taigi-tts-reports"

# 4-7. 清掉 VM 上上一次的報告資料夾
rm -rf /tmp/taigi-tts-reports

# 4-8. 把報告從 pod 拷到 VM
sudo kubectl -n care-dev cp taigi-tts-bench:/tmp/taigi-tts-reports /tmp/taigi-tts-reports

# 4-9. 把報告改成自己擁有，並確認有 .json 與 .md
sudo chown -R "$USER" /tmp/taigi-tts-reports
ls -la /tmp/taigi-tts-reports

# 4-10. 手動刪除暫時 pod：確認 4-9 看得到報告後再刪
sudo kubectl -n care-dev delete pod taigi-tts-bench

# 4-11. 再掃描一次：taigi-tts-bench 不應該出現，其他 pod 的狀態也應該和 4-1 一樣
sudo kubectl -n care-dev get pods
```

5. 本機：把報告拿回 repo（第一次要先建資料夾）：

```bash
mkdir -p evals/tts/reports
gcloud compute scp "care-vm:/tmp/taigi-tts-reports/*" evals/tts/reports/ --zone asia-east1-b --project project-09223ab4-b0ce-4aee-a99
```

注意：

- 4-3 若出現找不到 key，先用
  `sudo kubectl -n care-dev get secret care-backend-secret -o jsonpath='{.data}' | tr ',' '\n' | cut -d: -f1`
  看裡面有哪些 key（只列名稱，不會印出值）。本機的 CARE-infra 沒有 care-tts 的部署設定，`TAIGI_API_KEY`
  放在 `care-backend-secret` 是從 backend 整份掛這個 secret 推得的，第一次跑請先確認。
- 報告若出現大量失敗且錯誤是 HTTP 429，就是撞到速率限制：把 `--interval` 加大再跑一次。
