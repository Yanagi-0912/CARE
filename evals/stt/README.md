# STT 回應時間與錯字率量測

腳本：`scripts/speech_bench/chinese_stt_bench.py`（國語，正式程式的 GeminiTranscriber）。
報告：`reports/chinese-stt-<env-label>-YYYYMMDD-HHMMSS.json`（每一次的時間、逐字稿與錯字率）與同名 `.md`（摘要）。

## 量什麼、怎麼量

- 五句與國語 TTS 量測共用（`chinese_tts_bench.SENTENCES`，含標點剛好 10／25／50／100／150 字）。
- 每句先用 edge-tts 預設女聲念成 mp3（不計時），再交給 `gemini-3.5-flash-lite`（與 LINE 語音訊息同一個
  模型、prompt、8 秒逾時）轉文字，量「送出音檔 → 拿到逐字稿」的時間。
- 錯字率（CER）＝ 編輯距離 ÷ 原句字數，比對前去掉標點與空白；輸出簡體字會算錯。
- 音檔是合成語音，發音標準、沒有雜音，錯字率要當成**下限**；真人（尤其長輩）錄音只會更差。
- 150 字那句念出來約 40 秒，可能撞到正式程式的 8 秒逾時而記為失敗——那就是正式環境會發生的事
  （逾時後改走 faster-whisper），報告照實記錄。

## 費用

**會呼叫 Gemini。** 預設每種長度 1 次暖身＋5 次正式，共 30 次。用的是正式環境的 API 金鑰，與使用者共用
額度；次數很少，但仍建議避開使用高峰。edge-tts 免費。

## 在本機跑

需要 `.env` 裡的 `GEMINI_API_KEY`（腳本會自動讀）。

```bash
# PowerShell
.\.venv\Scripts\python.exe scripts\speech_bench\chinese_stt_bench.py --env-label local
# Git Bash
.venv/Scripts/python.exe scripts/speech_bench/chinese_stt_bench.py --env-label local
```

## 在 GCP 跑（暫時獨立 pod，不碰正式服務）

原則同 [evals/tts/README.md](../tts/README.md)：**千萬不要在 `care-backend` pod 裡跑**，用 backend 的映像開暫時 pod，
跑完手動刪掉。和 TTS 不同的兩點：

1. 兩支腳本都要複製進 pod（STT 腳本共用 TTS 腳本裡的五句與設定）。
2. 暫時 pod 預設沒有任何密鑰。開 pod 時只從 `care-backend-secret` 掛 **`GEMINI_API_KEY` 這一個值**，
   不帶整份密鑰；backend 本身完全不動。

```bash
# 1. 本機：把兩支腳本送到 VM 的 /tmp
gcloud compute scp scripts/speech_bench/chinese_tts_bench.py scripts/speech_bench/chinese_stt_bench.py care-vm:/tmp/ --zone asia-east1-b --project project-09223ab4-b0ce-4aee-a99

# 2. 登入 VM
gcloud compute ssh care-vm --zone asia-east1-b --project project-09223ab4-b0ce-4aee-a99
```

3. VM 上：一個指令做一件事，照順序執行。暫時 pod 最多 90 分鐘後自己結束（`sleep 5400`），結束後裡面的
   報告就消失，所以 3-3 到 3-8 要在 90 分鐘內做完；刪除 pod（3-10）自己手動執行。

```bash
# 3-1. 掃描現有的 pod：確認沒有上次殘留的 stt-bench（有的話先執行 3-10 刪掉）
sudo kubectl -n care-dev get pods

# 3-2. 取得 backend 正在用的映像（換了 SSH 視窗要重新執行）
IMAGE=$(sudo kubectl -n care-dev get deploy care-backend -o jsonpath='{.spec.template.spec.containers[0].image}')
echo "$IMAGE"

# 3-3. 開暫時 pod：上限 256Mi、0.5 核，只掛 GEMINI_API_KEY 一個密鑰值
sudo kubectl -n care-dev run stt-bench --image="$IMAGE" --restart=Never \
  --overrides='{"spec":{"containers":[{"name":"stt-bench","image":"'"$IMAGE"'","command":["sleep","5400"],"env":[{"name":"GEMINI_API_KEY","valueFrom":{"secretKeyRef":{"name":"care-backend-secret","key":"GEMINI_API_KEY"}}}],"resources":{"limits":{"memory":"256Mi","cpu":"500m"}}}]}}'

# 3-4. 等 pod 準備好（出現 condition met 再往下）
sudo kubectl -n care-dev wait --for=condition=Ready pod/stt-bench --timeout=120s

# 3-5. 把兩支腳本複製進 pod
sudo kubectl -n care-dev cp /tmp/chinese_tts_bench.py stt-bench:/tmp/chinese_tts_bench.py
sudo kubectl -n care-dev cp /tmp/chinese_stt_bench.py stt-bench:/tmp/chinese_stt_bench.py

# 3-6. 在 pod 裡執行量測（約 1～2 分鐘，會呼叫 Gemini 30 次）
sudo kubectl -n care-dev exec stt-bench -- sh -c "cd /app && python /tmp/chinese_stt_bench.py --env-label gcp-pod --out /tmp/stt-reports"

# 3-7. 清掉 VM 上上一次的報告資料夾
rm -rf /tmp/stt-reports

# 3-8. 把報告從 pod 拷到 VM
sudo kubectl -n care-dev cp stt-bench:/tmp/stt-reports /tmp/stt-reports

# 3-9. 把報告改成自己擁有，並確認有 .json 與 .md
sudo chown -R "$USER" /tmp/stt-reports
ls -la /tmp/stt-reports

# 3-10. 手動刪除暫時 pod：確認 3-9 看得到報告後再刪
sudo kubectl -n care-dev delete pod stt-bench

# 3-11. 再掃描一次：stt-bench 不應該出現，其他 pod 的狀態也應該和 3-1 一樣
sudo kubectl -n care-dev get pods
```

4. 本機：把報告拿回 repo（pscp 不會自動建立本機資料夾，第一次要先建）：

```bash
mkdir -p evals/stt/reports
gcloud compute scp "care-vm:/tmp/stt-reports/*" evals/stt/reports/ --zone asia-east1-b --project project-09223ab4-b0ce-4aee-a99
```

注意：

- 腳本靠工作目錄 `/app` 找 `app` 套件，一定要先 `cd /app`。
- 3-3 若出現 `secret "care-backend-secret" not found` 或找不到 key，先用
  `sudo kubectl -n care-dev get secret care-backend-secret -o jsonpath='{.data}' | tr ',' '\n' | cut -d: -f1`
  看裡面有哪些 key（只列名稱，不會印出值）。
- 看其他 namespace 有沒有殘留的暫時 pod：`sudo kubectl get pods -A`。
