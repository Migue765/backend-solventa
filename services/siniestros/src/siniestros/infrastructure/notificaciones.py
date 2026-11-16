"""Outbox transaccional de notificaciones al cliente y su publicacion idempotente."""

import json
import logging
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from http.client import HTTPException
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID, uuid5

from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.models import Claim, NotificationOutbox, ParametricPayment
from sqlalchemy import event, inspect, select, update
from sqlalchemy.orm import Session, sessionmaker

logger = logging.getLogger(__name__)
NAMESPACE_NOTIFICACIONES = UUID("3d0f1f0c-5a8e-4f43-9a55-8f1f0b6c2e71")
ESTADOS_NOTIFICABLES = frozenset(
    {"en_evaluacion", "en_revision", "en_inspeccion", "aprobado", "rechazado", "pagado", "cerrado"}
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat().replace("+00:00", "Z")


def registrar_outbox(sessions: sessionmaker[Session]) -> None:
    """Encola en la misma transaccion cada cambio notificable de siniestro o pago."""
    event.listen(sessions, "before_flush", _encolar)


def _encolar(session: Session, _flush_context: object, _instances: Iterable[object] | None) -> None:
    ahora = datetime.now(UTC)
    with session.no_autoflush:
        for objeto in list(session.new):
            if isinstance(objeto, CambioEstadoModel):
                _encolar_cambio_estado(session, objeto, ahora)
        for objeto in list(session.dirty):
            if isinstance(objeto, ParametricPayment):
                _encolar_pago(session, objeto, ahora)


def _encolar_cambio_estado(session: Session, cambio: CambioEstadoModel, ahora: datetime) -> None:
    if not cambio.visible_cliente or cambio.estado not in ESTADOS_NOTIFICABLES:
        return
    siniestro = session.get(Claim, cambio.siniestro_id)
    if siniestro is None:
        return
    _agregar(
        session,
        NotificationOutbox(
            evento_id=str(
                uuid5(
                    NAMESPACE_NOTIFICACIONES,
                    f"siniestro:{cambio.siniestro_id}:{cambio.secuencia}",
                )
            ),
            tipo="siniestro_estado_cambiado",
            cliente_id=siniestro.owner,
            recurso_tipo="siniestro",
            recurso_id=cambio.siniestro_id,
            estado=cambio.estado,
            ocurrido_en=cambio.ocurrido_en,
            creado_en=ahora,
            intentos=0,
        ),
    )


def _encolar_pago(session: Session, pago: ParametricPayment, ahora: datetime) -> None:
    if pago.estado != "confirmado" or "confirmado" not in inspect(pago).attrs.estado.history.added:
        return
    siniestro = session.get(Claim, pago.siniestro_id)
    if siniestro is None:
        return
    _agregar(
        session,
        NotificationOutbox(
            evento_id=str(uuid5(NAMESPACE_NOTIFICACIONES, f"pago:{pago.id}:confirmado")),
            tipo="pago_parametrico_ejecutado",
            cliente_id=siniestro.owner,
            recurso_tipo="pago",
            recurso_id=pago.id,
            estado=None,
            ocurrido_en=pago.finalizado_en or ahora,
            creado_en=ahora,
            intentos=0,
        ),
    )


def _agregar(session: Session, evento: NotificationOutbox) -> None:
    if session.get(NotificationOutbox, evento.evento_id) is None:
        session.add(evento)


def evento_notificacion(evento: NotificationOutbox) -> dict[str, Any]:
    cuerpo: dict[str, Any] = {
        "eventoId": evento.evento_id,
        "tipo": evento.tipo,
        "clienteId": evento.cliente_id,
        "ocurridoEn": _iso(evento.ocurrido_en),
        "recurso": {"tipo": evento.recurso_tipo, "id": evento.recurso_id},
    }
    if evento.estado is not None:
        cuerpo["estado"] = evento.estado
    return cuerpo


class EnviadorNotificaciones(Protocol):
    def enviar(self, evento: dict[str, Any]) -> bool: ...


class EnviadorNotificacionesHttp:
    def __init__(self, base_url: str, internal_token: str, timeout_seconds: float = 5) -> None:
        self._url = f"{base_url.rstrip('/')}/v1/eventos"
        self._internal_token = internal_token
        self._timeout_seconds = timeout_seconds

    def enviar(self, evento: dict[str, Any]) -> bool:
        cuerpo = json.dumps(evento, separators=(",", ":")).encode()
        solicitud = Request(
            self._url,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._internal_token}",
                "Content-Type": "application/json",
                "Content-Length": str(len(cuerpo)),
            },
            data=cuerpo,
        )
        try:
            try:
                respuesta = urlopen(solicitud, timeout=self._timeout_seconds)
            except HTTPError as error:
                respuesta = error
            with respuesta:
                contenido = respuesta.read(16 * 1024 + 1)
                if respuesta.status not in {200, 201} or len(contenido) > 16 * 1024:
                    return False
                resultado = json.loads(contenido)
                return (
                    isinstance(resultado, dict) and resultado.get("eventoId") == evento["eventoId"]
                )
        except (URLError, TimeoutError, HTTPException, ValueError, TypeError):
            logger.exception("notificaciones.publicacion_fallida evento_id=%s", evento["eventoId"])
            return False


class PublicadorNotificaciones:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        enviador: EnviadorNotificaciones,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._sessions = sessions
        self._enviador = enviador
        self._reloj = reloj

    def publicar(self, limite: int) -> dict[str, int]:
        with self._sessions() as session:
            pendientes = session.scalars(
                select(NotificationOutbox)
                .where(NotificationOutbox.publicado_en.is_(None))
                .order_by(
                    NotificationOutbox.intentos,
                    NotificationOutbox.creado_en,
                    NotificationOutbox.evento_id,
                )
                .limit(limite)
            ).all()
        publicadas = 0
        fallidas = 0
        for pendiente in pendientes:
            enviada = self._enviador.enviar(evento_notificacion(pendiente))
            with self._sessions.begin() as session:
                session.execute(
                    update(NotificationOutbox)
                    .where(
                        NotificationOutbox.evento_id == pendiente.evento_id,
                        NotificationOutbox.publicado_en.is_(None),
                    )
                    .values(
                        intentos=NotificationOutbox.intentos + 1,
                        publicado_en=self._reloj() if enviada else None,
                        ultimo_error=None if enviada else "publicacion_fallida",
                    )
                )
            publicadas += int(enviada)
            fallidas += int(not enviada)
            if not enviada:
                # Notificaciones no responde: el resto espera al siguiente ciclo.
                break
        return {"publicadas": publicadas, "fallidas": fallidas}
