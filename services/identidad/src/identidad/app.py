import hmac
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import click
from flask import Flask, Response, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from identidad.persistence import ConflictoVersionError, RepositorioConsentimientos

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    internal_api_token: str
    database_url: str = "sqlite:///identidad.db"

    def __post_init__(self) -> None:
        if not self.internal_api_token.strip():
            raise ValueError("INTERNAL_API_TOKEN es obligatorio.")

    @classmethod
    def desde_entorno(cls) -> "Config":
        return cls(
            internal_api_token=os.getenv("INTERNAL_API_TOKEN", ""),
            database_url=os.getenv("IDENTIDAD_DATABASE_URL", cls.database_url),
        )


class AutorizacionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    autorizado: StrictBool
    version: int = Field(ge=0, strict=True)


def create_app(config: Config | None = None) -> Flask:
    config = config or Config.desde_entorno()
    app = Flask(__name__)
    repositorio = RepositorioConsentimientos(config.database_url)
    app.extensions["consentimientos"] = repositorio

    @app.cli.command("init-db")
    def init_db() -> None:
        repositorio.inicializar()
        click.echo("Esquema inicial de Identidad creado.")

    @app.before_request
    def autenticar() -> tuple[Response, int] | None:
        g.correlation_id = request.headers.get("X-Correlation-ID") or str(uuid4())
        if request.path != "/health" and not hmac.compare_digest(
            request.headers.get("Authorization", ""),
            f"Bearer {config.internal_api_token}",
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

    @app.route(
        "/v1/clientes/<uuid:cliente_id>/consentimiento-perfilamiento", methods=["GET", "PUT"]
    )
    def consentimiento(cliente_id: UUID) -> tuple[Response, int] | Response:
        if request.method == "GET":
            actual = repositorio.obtener(cliente_id)
            if actual is None:
                return jsonify(error="consentimiento_ausente"), 404
            return jsonify(actual.model_dump(mode="json", by_alias=True))
        try:
            payload = AutorizacionRequest.model_validate(request.get_json())
        except (ValidationError, BadRequest, UnsupportedMediaType):
            return jsonify(error="solicitud_invalida"), 400
        try:
            registrado = repositorio.registrar(
                cliente_id, payload.autorizado, payload.version, datetime.now(UTC), g.correlation_id
            )
        except ConflictoVersionError:
            return jsonify(error="conflicto_version"), 409
        return jsonify(registrado.model_dump(mode="json", by_alias=True))

    @app.errorhandler(500)
    def error_interno(error: Exception) -> tuple[Response, int]:
        logger.error(
            "Fallo de Identidad", exc_info=error, extra={"correlationId": g.correlation_id}
        )
        return jsonify(error="servicio_no_disponible"), 503

    return app
