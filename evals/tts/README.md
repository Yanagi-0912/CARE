# TTS 回應時間量測

腳本：`scripts/speech_bench/chinese_tts_bench.py`（國語，正式程式的 edge-tts 引擎）。
報告：`reports/chinese-tts-<env-label>-YYYYMMDD-HHMMSS.json`（每一次的原始數據）與同名 `.md`（摘要）。

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
