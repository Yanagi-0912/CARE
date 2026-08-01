# 基底映像：Python 3.12 精簡版
FROM python:3.12-slim

# 不寫 .pyc、stdout 即時輸出、pip 不保留快取（映像較小）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 以非 root 使用者執行，降低容器遭入侵時可取得的權限
RUN groupadd --system --gid 1001 care \
    && useradd --system --uid 1001 --gid care --home /nonexistent --shell /usr/sbin/nologin care

# 工作目錄
WORKDIR /app

# 先複製依賴清單並安裝，利於 Docker layer 快取（程式碼變動時不必重裝套件）
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

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
