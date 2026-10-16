FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY services/cotizacion ./services/cotizacion
COPY services/bff-web ./services/bff-web
COPY services/identidad ./services/identidad
COPY services/perfilamiento ./services/perfilamiento
COPY deploy/hu11/init_database.py ./init_database.py

RUN pip install "./services/cotizacion[postgres,server]" "./services/bff-web" \
    "./services/identidad[postgres]" "./services/perfilamiento[postgres]" \
    && useradd --system --uid 10001 --no-create-home solventa

USER 10001
EXPOSE 8080
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:8080 --workers 1 --threads 4 --access-logfile - \"$SERVICE_APP\""]
