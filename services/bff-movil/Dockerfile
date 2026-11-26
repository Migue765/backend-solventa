FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY services/bff-web/pyproject.toml ./bff-web/
COPY services/bff-web/src ./bff-web/src
COPY services/bff-movil/pyproject.toml ./bff-movil/
COPY services/bff-movil/src ./bff-movil/src
RUN pip install ./bff-web "./bff-movil[server]"

RUN useradd --system --uid 10001 --no-create-home bff-movil
USER 10001

ENV WEB_CONCURRENCY=4 \
    GUNICORN_THREADS=4

EXPOSE 8080

CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:8080 --workers ${WEB_CONCURRENCY} --threads ${GUNICORN_THREADS} --access-logfile - 'bff_movil.journey_app:create_app()'"]
