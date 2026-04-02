FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
RUN pip install --upgrade pip && pip install '.[dev]'

ARG APP_VERSION=dev
RUN echo "${APP_VERSION}" > /app/VERSION

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
