FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential \
      libpq-dev \
      curl \
      ca-certificates \
      tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY README.md /app/README.md

RUN pip install --upgrade pip \
    && pip install \
        "kurigram==2.2.18" \
        "aiogram>=3.4" \
        "sqlalchemy[asyncio]>=2.0.30" \
        "asyncpg>=0.29" \
        "alembic>=1.13" \
        "pydantic>=2.6" \
        "pydantic-settings>=2.2" \
        "python-dotenv>=1.0" \
        "loguru>=0.7" \
        "cryptography>=42.0" \
        "pyyaml>=6.0" \
        "aiohttp>=3.9" \
        "click>=8.1" \
        "python-socks[asyncio]>=2.4" \
        "tgcrypto>=1.2.5" \
        "aiohttp-socks>=0.9" \
        "opentele>=1.15"

COPY app /app/app
COPY migrations /app/migrations
COPY alembic.ini /app/alembic.ini
COPY config.yaml /app/config.yaml

RUN mkdir -p /app/sessions /app/logs /app/dumps /app/data

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "app.main"]
