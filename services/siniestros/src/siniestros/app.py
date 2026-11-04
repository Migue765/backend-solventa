import hmac
import logging
import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

from flask import Flask, Response, g, jsonify, request
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from siniestros.domain import AvisoSiniestro, ErrorSiniestro, validar_evento
from siniestros.polizas_http import PolizasHttp
from siniestros.repositorio import RepositorioSiniestros

logger = logging.getLogger(__name__)


def create_app(
    database_url: str | None = None,
    internal_token: str | None = None,
    polizas_url: str | None = None,
) -> Flask:
    url = database_url or os.environ["SINIESTROS_DATABASE_URL"]
    token = internal_token if internal_token is not None else os.environ["INTERNAL_API_TOKEN"]
    servicio_polizas = polizas_url or os.environ["POLIZAS_SERVICE_URL"]
    if not token.strip() or not servicio_polizas.strip():
        raise ValueError("INTERNAL_API_TOKEN y POLIZAS_SERVICE_URL son obligatorios.")
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 65536
    engine = create_engine(url, pool_pre_ping=True)
    repositorio = RepositorioSiniestros(sessionmaker(engine, expire_on_commit=False))
    polizas = PolizasHttp(servicio_polizas, token)
    app.extensions["siniestros_engine"] = engine
    app.extensions["siniestros_repository"] = repositorio

    @app.before_request
    def autenticar() -> tuple[Response, int] | None:
        g.correlation_id = request.headers.get("X-Correlation-ID") or str(uuid4())
        if request.path != "/health" and not hmac.compare_digest(
            request.headers.get("Authorization", "").encode(), f"Bearer {token}".encode()
        ):
            return jsonify(error="no_autorizado", correlationId=g.correlation_id), 401
        return None

    @app.after_request
    def cabeceras(response: Response) -> Response:
        response.headers["X-Correlation-ID"] = g.correlation_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    def health() -> Response:
        return jsonify(status="ok")

    @app.post("/v1/siniestros")
    def registrar() -> tuple[Response, int]:
        if request.args:
            raise ValueError("El registro no admite parametros.")
        cuerpo = request.get_json()
        if not isinstance(cuerpo, dict):
            raise ValueError("Se requiere un objeto JSON.")
        cliente_id: UUID = TypeAdapter(UUID).validate_python(cuerpo.pop("clienteId", None))
        clave = UUID(request.headers.get("Idempotency-Key", ""))
        aviso = AvisoSiniestro.model_validate(cuerpo)
        existente = repositorio.recuperar(cliente_id, clave, aviso)
        autorizadas = polizas.listar(cliente_id, g.correlation_id)
        poliza = next((p for p in autorizadas if p.id == aviso.poliza_id), None)
        if poliza is None:
            raise ErrorSiniestro("poliza_no_encontrada", 404)
        if existente is not None:
            return jsonify(existente), 200
        ahora = datetime.now(UTC)
        validar_evento(aviso, poliza, ahora)
        registro, creado = repositorio.registrar(cliente_id, clave, aviso, ahora)
        return jsonify(registro), 201 if creado else 200

    def cliente_consulta() -> UUID:
        if (
            set(request.args) != {"clienteId"}
            or len(request.args.getlist("clienteId")) != 1
            or request.get_data()
        ):
            raise ValueError("Se requiere un unico clienteId y ningun cuerpo.")
        return UUID(request.args["clienteId"])

    @app.get("/v1/siniestros")
    def listar() -> Response:
        cliente_id = cliente_consulta()
        autorizadas = polizas.listar(cliente_id, g.correlation_id)
        return jsonify(siniestros=repositorio.listar(cliente_id, [p.id for p in autorizadas]))

    @app.get("/v1/siniestros/polizas")
    def listar_polizas() -> Response:
        autorizadas = polizas.listar(cliente_consulta(), g.correlation_id)
        return jsonify(
            polizas=[
                p.model_dump(mode="json", by_alias=True)
                for p in autorizadas
                if p.estado == "vigente"
            ]
        )

    @app.get("/v1/siniestros/<uuid:siniestro_id>")
    def consultar(siniestro_id: UUID) -> Response:
        cliente_id = cliente_consulta()
        autorizadas = polizas.listar(cliente_id, g.correlation_id)
        return jsonify(repositorio.consultar(cliente_id, siniestro_id, [p.id for p in autorizadas]))

    @app.errorhandler(ValueError)
    @app.errorhandler(ValidationError)
    @app.errorhandler(BadRequest)
    @app.errorhandler(UnsupportedMediaType)
    def invalida(_: Exception) -> tuple[Response, int]:
        return jsonify(error="solicitud_invalida", correlationId=g.correlation_id), 400

    @app.errorhandler(ErrorSiniestro)
    def negocio(error: ErrorSiniestro) -> tuple[Response, int]:
        if error.estado == 503:
            logger.exception(
                "Fallo de dependencia de siniestros", extra={"correlationId": g.correlation_id}
            )
        return jsonify(error=error.codigo, correlationId=g.correlation_id), error.estado

    @app.errorhandler(SQLAlchemyError)
    def persistencia(_: SQLAlchemyError) -> tuple[Response, int]:
        logger.exception(
            "Fallo de persistencia de siniestros", extra={"correlationId": g.correlation_id}
        )
        return jsonify(error="servicio_no_disponible", correlationId=g.correlation_id), 503

    return app
