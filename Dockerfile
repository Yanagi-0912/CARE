FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 非 root 執行，降低容器被攻破時權限（Sonar docker:S6471）。
RUN groupadd --system --gid 1001 care \
    && useradd --system --uid 1001 --gid care --home /nonexistent --shell /usr/sbin/nologin care

WORKDIR /app

# Install Python dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Copy application source.
COPY app ./app

RUN chown -R care:care /app

USER care

EXPOSE 8000

# Production-style default command (no --reload).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
