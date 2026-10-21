"""Entrega al PEP existente; ejecutar cada minuto con el mismo almacen de Identidad."""

import argparse
import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from http.client import HTTPException
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import UUID

from sqlalchemy import case, select
from sqlalchemy.orm import Session, sessionmaker

from identidad.infrastructure.persistence.database import create_sessions
from identidad.infrastructure.persistence.models import ConsentEventModel

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 5


def validate_ack(ack: dict[str, object], event: dict[str, object]) -> None:
    version = ack.get("versionAplicada")
    applied = ack.get("aplicadaEn")
    if (
        ack.get("eventoId") != event["eventoId"]
        or type(version) is not int
        or version < int(str(event["version"]))
        or not isinstance(applied, str)
    ):
        raise ValueError("acuse_invalido")
    applied_at = datetime.fromisoformat(applied)
    effective_at = datetime.fromisoformat(str(event["efectivaEn"]))
    if applied_at.tzinfo is None or not effective_at <= applied_at <= datetime.now(UTC):
        raise ValueError("acuse_invalido")


def publish_http(url: str, token: str, event: dict[str, object]) -> dict[str, object]:
    request = Request(
        url.rstrip("/") + "/v1/eventos/consentimientos",
        data=json.dumps(event).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Correlation-ID": str(event["correlationId"]),
        },
        method="POST",
    )
    with urlopen(request, timeout=0.7) as response:
        ack = json.load(response)
    if not isinstance(ack, dict):
        raise ValueError("acuse_invalido")
    validate_ack(ack, event)
    return {str(key): value for key, value in ack.items()}


class ConsentOutbox:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        publish: Callable[[dict[str, object]], dict[str, object]],
    ) -> None:
        self._sessions = sessions
        self._publish = publish

    def deliver(self, retry_failed: bool = False) -> tuple[int, int]:
        delivered = failed = 0
        with self._sessions() as session:
            query = select(ConsentEventModel.evento_id).where(
                ConsentEventModel.publicado.is_(False)
            )
            if not retry_failed:
                query = query.where(ConsentEventModel.intentos < MAX_ATTEMPTS)
            ids = list(
                session.scalars(
                    query.order_by(
                        case((ConsentEventModel.tipo != "ConsentimientoAutorizado", 0), else_=1),
                        ConsentEventModel.contenido["efectivaEn"].as_string(),
                        ConsentEventModel.evento_id,
                    ).limit(50)
                )
            )
        for event_id in ids:
            result = self._deliver_one(event_id, retry_failed)
            if result is True:
                delivered += 1
            elif result is False:
                failed += 1
        with self._sessions() as session:
            dead = session.scalar(
                select(ConsentEventModel.evento_id)
                .where(
                    ConsentEventModel.publicado.is_(False),
                    ConsentEventModel.intentos >= MAX_ATTEMPTS,
                )
                .limit(1)
            )
        if dead is not None:
            logger.error("Outbox con eventos en DLQ; ejecutar --reintentar-fallidos.")
            failed = max(failed, 1)
        return delivered, failed

    def _deliver_one(self, event_id: UUID, retry_failed: bool = False) -> bool | None:
        with self._sessions.begin() as session:
            row = session.scalar(
                select(ConsentEventModel)
                .where(ConsentEventModel.evento_id == event_id)
                .with_for_update(skip_locked=True)
            )
            if row is None or row.publicado:
                return None
            if row.intentos >= MAX_ATTEMPTS and not retry_failed:
                return None
            content = row.contenido
            event = {
                key: content[key]
                for key in (
                    "clienteId",
                    "consentimientoId",
                    "version",
                    "estado",
                    "efectivaEn",
                    "correlationId",
                )
            }
            event.update(eventoId=str(row.evento_id), tipo=row.tipo)
            row.intentos += 1
            try:
                ack = self._publish(event)
                validate_ack(ack, event)
            except (URLError, OSError, ValueError, HTTPException) as error:
                row.ultimo_error = type(error).__name__
                logger.error(
                    "Entrega de consentimiento fallida evento=%s intento=%s causa=%s",
                    event_id,
                    row.intentos,
                    row.ultimo_error,
                )
                return False
            row.acuse = ack
            row.publicado = True
            row.ultimo_error = None
            return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reintentar-fallidos", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    token = os.environ["INTERNAL_API_TOKEN"].strip()
    url = os.environ["PERFILAMIENTO_SERVICE_URL"].strip()
    if not token or not url:
        raise ValueError("Token y destino del Outbox son obligatorios.")
    sessions = create_sessions(os.environ["DATABASE_URL"], os.getenv("DB_SCHEMA"), 1, 0)
    delivered, failed = ConsentOutbox(
        sessions, lambda event: publish_http(url, token, event)
    ).deliver(args.reintentar_fallidos)
    logger.info("Outbox: entregados=%s fallidos=%s", delivered, failed)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
