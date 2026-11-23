# TradeCognix backend (FastAPI). Build: docker build -t tradecognix-api backend
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Asia/Kolkata
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends tzdata curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-brokers.txt ./
ARG INSTALL_BROKER_SDKS=true
RUN pip install -r requirements.txt && if [ "$INSTALL_BROKER_SDKS" = "true" ]; then pip install -r requirements-brokers.txt; fi

COPY app ./app
COPY scripts ./scripts

RUN useradd -m -u 1000 tc && mkdir -p /app/.cache /app/data && chown -R tc /app
USER tc

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -fs http://localhost:${PORT:-8000}/health || exit 1
# One worker: the trading scheduler must run in exactly one process.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers"]
