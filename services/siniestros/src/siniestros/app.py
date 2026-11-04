import hmac
import logging
import os
from uuid import UUID, uuid4

from flask import Flask, Response, g, jsonify, request
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from werkzeug.exceptions import BadRequest

from siniestros.domain.estado import SiniestroNoIntegro
from siniestros.infrastructure.repositorio import RepositorioSiniestros

logger = logging.getLogger(__name__)


def create_app(database_url: str | None = None, internal_token: str | None = None) -> Flask:
    url = database_url or os.environ["SINIESTROS_DATABASE_URL"]
    token = internal_token if internal_token is not None else os.environ["INTERNAL_API_TOKEN"]
    if not token.strip():
        raise ValueError("INTERNAL_API_TOKEN es obligatorio.")
    app = Flask(__name__)
    engine = create_engine(url, pool_pre_ping=True)
    repositorio = RepositorioSiniestros(sessionmaker(engine, expire_on_commit=False))
    app.extensions["siniestros_engine"] = engine
    app.extensions["siniestros_repository"] = repositorio

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

    @app.get("/v1/siniestros/<siniestro_id>")
    def consultar(siniestro_id: str) -> Response | tuple[Response, int]:
        if set(request.args) != {"clienteId"} or len(request.args.getlist("clienteId")) != 1:
            raise ValueError("Se requiere un unico clienteId.")
        if request.get_data():
            raise ValueError("La consulta no admite cuerpo.")
        consulta = repositorio.consultar(UUID(siniestro_id), UUID(request.args["clienteId"]))
        if consulta is None:
            return jsonify(error="siniestro_no_encontrado", correlationId=g.correlation_id), 404
        return jsonify(consulta)

    @app.errorhandler(ValueError)
    @app.errorhandler(BadRequest)
    def invalida(_: Exception) -> tuple[Response, int]:
        return jsonify(error="solicitud_invalida", correlationId=g.correlation_id), 400

    @app.errorhandler(SiniestroNoIntegro)
    def no_integro(error: SiniestroNoIntegro) -> tuple[Response, int]:
        logger.error("Siniestro no integro: %s", error, extra={"correlationId": g.correlation_id})
        return jsonify(error="siniestro_no_integro", correlationId=g.correlation_id), 503

    @app.errorhandler(SQLAlchemyError)
    def persistencia(_: SQLAlchemyError) -> tuple[Response, int]:
        logger.exception(
            "Fallo de persistencia de siniestros", extra={"correlationId": g.correlation_id}
        )
        return jsonify(error="servicio_no_disponible", correlationId=g.correlation_id), 503

    return app
