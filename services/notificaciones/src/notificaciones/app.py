import hmac
import os
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from flask import Flask, Response, current_app, g, jsonify, request
from pydantic import ValidationError
from sqlalchemy import create_engine, text

from notificaciones.domain import NotificationEvent, PreferenceRequest
from notificaciones.models import Base
from notificaciones.repository import EventConflictError, NotificationRepository
from notificaciones.service import ConfiguredDeliveryGateway, DeliveryGateway, NotificationService

Clock = Callable[[], datetime]


def create_app(
    database_url: str | None = None,
    internal_token: str | None = None,
    delivery_gateway: DeliveryGateway | None = None,
    clock: Clock | None = None,
    create_schema: bool = False,
) -> Flask:
    url = database_url or _required("NOTIFICACIONES_DATABASE_URL")
    token = internal_token or _required("INTERNAL_API_TOKEN")
    engine = create_engine(url)
    if create_schema:
        Base.metadata.create_all(engine)
    repository = NotificationRepository(engine)
    service = NotificationService(
        repository,
        delivery_gateway or ConfiguredDeliveryGateway(),
        _positive_int("EXPIRATION_NOTICE_DAYS", 7),
        _positive_int("RENEWAL_NOTICE_DAYS", 30),
        _positive_int("NOTIFICATION_MAX_ATTEMPTS", 5),
    )
    active_clock = clock or (lambda: datetime.now(UTC))

    app = Flask(__name__)
    app.extensions["engine"] = engine
    app.extensions["repository"] = repository
    app.extensions["notification_service"] = service
    app.extensions["clock"] = active_clock
    app.extensions["internal_token"] = token

    @app.before_request
    def authorize() -> Response | None:
        g.correlation_id = _correlation_id(request.headers.get("X-Correlation-ID"))
        if request.path in {"/ping", "/health"}:
            return None
        authorization = request.headers.get("Authorization", "")
        expected = f"Bearer {current_app.extensions['internal_token']}"
        if not hmac.compare_digest(authorization, expected):
            return problem(
                401,
                "no_autorizado",
                "No autorizado",
                "Credenciales internas inválidas.",
            )
        return None

    @app.after_request
    def include_headers(response: Response) -> Response:
        response.headers["X-Correlation-ID"] = str(g.correlation_id)
        if request.path.startswith("/v1/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/ping")
    def ping() -> tuple[dict[str, str], int]:
        return {"status": "ok"}, 200

    @app.get("/health")
    def health() -> tuple[dict[str, str], int]:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok"}, 200

    @app.post("/v1/eventos")
    def receive_event() -> tuple[Response, int]:
        event = NotificationEvent.model_validate(request.get_json(silent=False))
        duplicate, generated, delivered = service.receive(event, active_clock())
        payload = {
            "eventoId": str(event.event_id),
            "duplicado": duplicate,
            "notificacionesGeneradas": generated,
            "entregasConfirmadas": delivered,
        }
        return jsonify(payload), 200 if duplicate else 201

    @app.post("/v1/despachos")
    def dispatch() -> tuple[Response, int]:
        delivered = service.dispatch(active_clock(), limit=100)
        return jsonify({"entregasConfirmadas": delivered}), 200

    @app.get("/v1/notificaciones")
    def list_notifications() -> tuple[Response, int]:
        client_id = _client_id()
        return jsonify({"notificaciones": repository.list_notifications(client_id)}), 200

    @app.get("/v1/notificaciones/preferencias")
    def list_preferences() -> tuple[Response, int]:
        client_id = _client_id()
        return jsonify({"preferencias": repository.preferences(client_id)}), 200

    @app.put("/v1/notificaciones/preferencias")
    def update_preferences() -> tuple[Response, int]:
        client_id = _client_id()
        body = PreferenceRequest.model_validate(request.get_json(silent=False))
        repository.update_preferences(client_id, body.preferences, active_clock())
        return jsonify({"preferencias": repository.preferences(client_id)}), 200

    @app.errorhandler(ValidationError)
    def invalid_payload(_: ValidationError) -> tuple[Response, int]:
        return problem(
            422,
            "evento_invalido",
            "Evento inválido",
            "Los datos no cumplen el contrato de notificaciones.",
        ), 422

    @app.errorhandler(EventConflictError)
    def event_conflict(_: EventConflictError) -> tuple[Response, int]:
        return problem(
            409,
            "evento_en_conflicto",
            "Evento en conflicto",
            "El identificador ya fue recibido con otros datos.",
        ), 409

    @app.errorhandler(400)
    def invalid_json(_: Exception) -> tuple[Response, int]:
        return problem(
            400,
            "solicitud_invalida",
            "Solicitud inválida",
            "El cuerpo no contiene JSON válido.",
        ), 400

    @app.errorhandler(ValueError)
    def invalid_header(_: ValueError) -> tuple[Response, int]:
        return problem(
            400,
            "cliente_invalido",
            "Cliente inválido",
            "X-Cliente-ID debe ser un UUID válido.",
        ), 400

    return app


def problem(status: int, code: str, title: str, detail: str) -> Response:
    response = jsonify(
        {
            "type": f"https://solventa.co/problemas/{code}",
            "title": title,
            "status": status,
            "detail": detail,
            "code": code,
            "correlationId": str(g.correlation_id),
        }
    )
    response.status_code = status
    response.content_type = "application/problem+json"
    return response


def _client_id() -> UUID:
    value = request.headers.get("X-Cliente-ID", "")
    if not value:
        raise ValueError
    return UUID(value)


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} es obligatoria.")
    return value


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} debe ser mayor que cero.")
    return value


def _correlation_id(value: str | None) -> UUID:
    if value:
        try:
            return UUID(value)
        except ValueError:
            pass
    return uuid4()
