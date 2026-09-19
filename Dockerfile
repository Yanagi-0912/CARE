# 基底映像：Python 3.12 精簡版
FROM python:3.12-slim

# uv 以固定版本從官方映像複製進來，確保 build 可重現（不隨 latest 漂移）
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /uvx /bin/

# 不寫 .pyc 由 uv 的 bytecode 預編譯取代（啟動較快）；stdout 即時輸出；
# UV_LINK_MODE=copy 避免跨檔案系統 hardlink 警告；容器內不留 uv 快取（映像較小）
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_CACHE=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# 以非 root 使用者執行，降低容器遭入侵時可取得的權限
RUN groupadd --system --gid 1001 care \
    && useradd --system --uid 1001 --gid care --home /nonexistent --shell /usr/sbin/nologin care

# 工作目錄
WORKDIR /app

# 先複製依賴清單並安裝，利於 Docker layer 快取（程式碼變動時不必重裝套件）
# --locked：uv.lock 與 pyproject.toml 不一致就讓 build 失敗，而非默默重解版本
# --no-dev：正式映像不含 pytest 等測試相依
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev

# 讓 uvicorn 等執行檔直接可用，CMD 不必前綴 uv run
ENV PATH="/app/.venv/bin:$PATH"

# 句向量模型（本地分類器的第二種特徵，見 app/services/guardrail/text_encoder.py）。
# 135MB 的二進位檔不進 git，在這裡下載——放在 COPY app 之前，程式碼變動時
# 這一層才不會失效重抓。腳本只用標準函式庫，且自帶重試（build 期的網路失敗
# 會直接擋住部署，kubeconform 那次就是這樣壞的）。
COPY scripts/fetch_text_encoder.py ./scripts/fetch_text_encoder.py
RUN python scripts/fetch_text_encoder.py

# 向量同步腳本：由 CARE-infra 的 care-vector-sync CronJob 以這個 image 執行
# （command 是 python scripts/sync_vectors_to_pg.py）。上面那行只複製了
# fetch_text_encoder.py 單一檔案，所以這裡必須明著再加一個——2026-09-19
# 第一次部署時就是漏了這行，Job 起來報 No such file or directory。
COPY scripts/sync_vectors_to_pg.py ./scripts/sync_vectors_to_pg.py

# 複製應用程式原始碼（含 Flex Message 等 top-level resources）
COPY app ./app
COPY resources ./resources

# 目錄擁有者改為 care，與下方 USER 一致
RUN chown -R care:care /app

USER care

# 對外提供服務的埠（與 uvicorn 一致）
EXPOSE 8000

# 正式環境預設：不使用 --reload
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
