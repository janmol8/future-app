FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOST=0.0.0.0 \
    FLASK_DEBUG=0 \
    FUTURE_APP_DATA_DIR=/tmp/data

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend backend
COPY frontend frontend
COPY models models
# stock_provider reads this relative to the working directory
COPY nse_master_cache.json nse_master_cache.json

# No hardcoded PORT: the platform injects it and app.py reads it.
CMD ["python", "backend/app.py"]
