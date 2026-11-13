# app2-notibot/Dockerfile
# Python 3.12: Scrapling (scraping/dom.py) exige >= 3.10 y 3.9 está sin soporte.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DIARIOS_CONFIG_PATH=/app/config/diarios.json \
    TOKENS_PATH=/app/token_jwt/tokens.json

WORKDIR /app

# deps mínimas de sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tzdata && \
    rm -rf /var/lib/apt/lists/*

# Python deps primero para cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código
COPY . /app/

CMD ["python", "bot_telegram.py"]
