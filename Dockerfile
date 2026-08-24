FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CLULATENT_DATA_DIR=/data/clulatent-api

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -e ".[api]"

RUN mkdir -p /data/clulatent-api

EXPOSE 8000
CMD ["sh", "-c", "uvicorn clu_latent.http_api:app --host 0.0.0.0 --port ${PORT:-8000}"]
