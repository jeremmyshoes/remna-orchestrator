FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
COPY templates ./templates

RUN pip install --upgrade pip && pip install .

RUN mkdir -p /app/data

EXPOSE 8080

CMD ["uvicorn", "app.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
