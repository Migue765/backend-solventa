import hmac
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

from flask import Flask, Response, g, jsonify, request
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from polizas.domain.emision import EvidenciaEmision
from polizas.domain.renovacion import SolicitudRenovacion
from polizas.infrastructure.renovaciones import (
    CondicionesCambiaron,
    IdempotenciaConflictiva,
    PasarelaPagoRenovacion,
    PolizaNoEncontrada,
    PolizaNoRenovable,
    RenovacionEnCurso,
    RepositorioRenovaciones,
    SimuladorPagoRenovacion,
)
from polizas.infrastructure.repositorio import ConflictoEmision, RepositorioPolizas

logger = logging.getLogger(__name__)


def create_app(
    database_url: str | None = None,
    internal_token: str | None = None,
    pasarela: PasarelaPagoRenovacion | None = None,
    reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Flask:
    url = database_url or os.environ["POLIZAS_DATABASE_URL"]
    token = internal_token if internal_token is not None else os.environ["INTERNAL_API_TOKEN"]
    if not token.strip():
        raise ValueError("INTERNAL_API_TOKEN es obligatorio.")
    app = Flask(__name__)
    engine = create_engine(url, pool_pre_ping=True)
    sesiones = sessionmaker(engine, expire_on_commit=False)
    repositorio = RepositorioPolizas(sesiones)
    renovaciones = RepositorioRenovaciones(
        sesiones,
        pasarela
        or SimuladorPagoRenovacion(
            sesiones, os.getenv("PAGO_RENOVACION_SIMULADO", "confirmado").strip()
        ),
    )
    app.extensions["polizas_engine"] = engine
    app.extensions["polizas_repository"] = repositorio
    app.extensions["polizas_renovaciones"] = renovaciones

    @app.before_request
    def autenticar() -> tuple[Response, int] | None:
        g.correlation_id = request.headers.get("X-Correlation-ID") or str(uuid4())
        if request.path != "/health" and not hmac.compare_digest(
            request.headers.get("Authorization", "").encode(), f"Bearer {token}".encode()
        ):
            return jsonify(error="no_autorizado"), 401
        return None

    @app.after_request
    def cabeceras(response: Response) -> Response:
        response.headers["X-Correlation-ID"] = g.correlation_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health() -> Response:
        return jsonify(status="ok")

    @app.get("/v1/polizas")
    def listar() -> Response:
        if set(request.args) != {"clienteId"} or len(request.args.getlist("clienteId")) != 1:
            raise ValueError("Se requiere un unico clienteId.")
        if request.get_data():
            raise ValueError("La consulta no admite cuerpo.")
        cliente_id = UUID(request.args["clienteId"])
        return jsonify(polizas=repositorio.listar(cliente_id, datetime.now(UTC)))

    @app.post("/v1/polizas")
    def emitir() -> Response:
        if request.args:
            raise ValueError("La emision no admite parametros.")
        evidencia = EvidenciaEmision.model_validate(request.get_json())
        return jsonify(repositorio.emitir(evidencia, datetime.now(UTC)))

    @app.get("/v1/polizas/<uuid:poliza_id>/renovacion")
    def consultar_renovacion(poliza_id: UUID) -> Response:
        if request.args or request.get_data():
            raise ValueError("La consulta no admite parametros ni cuerpo.")
        return jsonify(renovaciones.consultar(poliza_id, _cliente(), reloj()))

    @app.post("/v1/polizas/<uuid:poliza_id>/renovaciones")
    def renovar(poliza_id: UUID) -> tuple[Response, int]:
        if request.args:
            raise ValueError("La renovacion no admite parametros.")
        cliente_id = _cliente()
        clave = UUID(request.headers.get("Idempotency-Key", ""))
        solicitud = SolicitudRenovacion.model_validate(request.get_json())
        resultado, nueva = renovaciones.renovar(poliza_id, cliente_id, clave, solicitud, reloj())
        if resultado["estado"] == "pendiente":
            return jsonify(resultado), 202
        return jsonify(resultado), 201 if nueva else 200

    @app.cli.command("conciliar-renovaciones")
    def conciliar_renovaciones() -> None:
        print(f"renovaciones_conciliadas={renovaciones.conciliar_pendientes()}")

    def _cliente() -> UUID:
        return UUID(request.headers.get("X-Cliente-ID", ""))

    @app.errorhandler(ValueError)
    @app.errorhandler(ValidationError)
    @app.errorhandler(BadRequest)
    @app.errorhandler(UnsupportedMediaType)
    def invalida(_: Exception) -> tuple[Response, int]:
        return jsonify(error="solicitud_invalida", correlationId=g.correlation_id), 400

    @app.errorhandler(ConflictoEmision)
    def conflicto(_: ConflictoEmision) -> tuple[Response, int]:
        return jsonify(error="conflicto_emision", correlationId=g.correlation_id), 409

    @app.errorhandler(PolizaNoEncontrada)
    def no_encontrada(_: PolizaNoEncontrada) -> tuple[Response, int]:
        return jsonify(error="poliza_no_encontrada", correlationId=g.correlation_id), 404

    @app.errorhandler(PolizaNoRenovable)
    def no_renovable(error: PolizaNoRenovable) -> tuple[Response, int]:
        return jsonify(
            error="poliza_no_renovable", motivo=error.motivo, correlationId=g.correlation_id
        ), 422

    @app.errorhandler(CondicionesCambiaron)
    def condiciones_cambiaron(_: CondicionesCambiaron) -> tuple[Response, int]:
        return jsonify(error="condiciones_cambiaron", correlationId=g.correlation_id), 409

    @app.errorhandler(IdempotenciaConflictiva)
    def idempotencia(_: IdempotenciaConflictiva) -> tuple[Response, int]:
        return jsonify(error="idempotencia_conflictiva", correlationId=g.correlation_id), 409

    @app.errorhandler(RenovacionEnCurso)
    def en_curso(error: RenovacionEnCurso) -> tuple[Response, int]:
        renovacion_id = str(error.renovacion_id) if error.renovacion_id else None
        return jsonify(
            error="renovacion_en_curso", renovacionId=renovacion_id, correlationId=g.correlation_id
        ), 409

    @app.errorhandler(SQLAlchemyError)
    @app.errorhandler(RuntimeError)
    def persistencia(_: Exception) -> tuple[Response, int]:
        logger.exception(
            "Fallo de persistencia de polizas", extra={"correlationId": g.correlation_id}
        )
        return jsonify(error="servicio_no_disponible", correlationId=g.correlation_id), 503

    return app
