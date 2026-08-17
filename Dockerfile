# Prospector — otimizado para Render free tier (512 MB RAM)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    LOW_MEM=1 \
    PARALELO=1 \
    MALLOC_ARENA_MAX=2

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    # headless-shell: build mínimo do Chromium (~150 MB a menos que o completo)
    && playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache

COPY . .

# Render injeta $PORT; 1 worker é essencial (cada worker subiria seu próprio Chromium)
CMD uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1
