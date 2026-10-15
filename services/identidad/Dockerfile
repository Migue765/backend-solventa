FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml alembic.ini ./
COPY src ./src
COPY migrations ./migrations
RUN pip install ".[postgres,server]"

RUN useradd --system --uid 10001 --no-create-home identidad
USER 10001

ENV WEB_CONCURRENCY=4 \
    GUNICORN_THREADS=4

EXPOSE 8080

CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:8080 --workers ${WEB_CONCURRENCY} --threads ${GUNICORN_THREADS} --access-logfile - 'identidad.app:create_app()'"]
