import hmac
import logging
import os
from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

import click
from flask import Flask, Response, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator
from werkzeug.exceptions import BadRequest, UnsupportedMediaType

from perfilamiento.adapters import ConsentimientosHttp, FuenteMock
from perfilamiento.application import ConsultarPerfil
from perfilamiento.client_information import (
    ConsultarInformacionCliente,
    ConsultarInformacionComando,
)
from perfilamiento.domain import ErrorPerfilamiento, EventoConsentimiento
from perfilamiento.exceptions import (
    AbrirExcepcionOpenFinance,
    GestionarExcepcionesOpenFinance,
    ResolverExcepcionOpenFinance,
)
from perfilamiento.persistence import RepositorioPerfiles
from perfilamiento.ports import Consentimientos, FuentePerfilamiento

logger = logging.getLogger(__name__)
ESCENARIO: TypeAdapter[Literal["disponible", "no_disponible", "vencido"]] = TypeAdapter(
    Literal["disponible", "no_disponible", "vencido"]
)


@dataclass(frozen=True)
class Config:
    internal_api_token: str
    database_url: str = "sqlite:///perfilamiento.db"
    identidad_url: str = "http://localhost:5002"
    mock_escenario: Literal["disponible", "no_disponible", "vencido"] = "disponible"

    def __post_init__(self) -> None:
        if not self.internal_api_token.strip():
            raise ValueError("INTERNAL_API_TOKEN es obligatorio.")
        if self.mock_escenario not in {"disponible", "no_disponible", "vencido"}:
            raise ValueError("PERFILAMIENTO_MOCK_ESCENARIO no es valido.")

    @classmethod
    def desde_entorno(cls) -> "Config":
        escenario = ESCENARIO.validate_python(
            os.getenv("PERFILAMIENTO_MOCK_ESCENARIO", "disponible")
        )
        return cls(
            internal_api_token=os.getenv("INTERNAL_API_TOKEN", ""),
            database_url=os.getenv("PERFILAMIENTO_DATABASE_URL", cls.database_url),
            identidad_url=os.getenv("IDENTIDAD_SERVICE_URL", cls.identidad_url),
            mock_escenario=escenario,
        )


class ConsultaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    cliente_id: UUID = Field(alias="clienteId")
    referencia_id: UUID = Field(alias="referenciaId")
    operation_id: UUID | None = Field(default=None, alias="operationId")
    credito_id: str | None = Field(default=None, alias="creditoId", min_length=1, max_length=64)
    consentimiento_id: UUID | None = Field(default=None, alias="consentimientoId")
    consentimiento_version: int | None = Field(default=None, alias="consentimientoVersion", ge=1)
    fuentes: tuple[str, ...] | None = Field(default=None, min_length=1, max_length=8)
    finalidad: str = "cotizacion"
    datos: tuple[str, ...] = ("indicador_riesgo",)

    @model_validator(mode="after")
    def consentimiento_completo(self) -> "ConsultaRequest":
        if (self.consentimiento_id is None) != (self.consentimiento_version is None):
            raise ValueError("El consentimiento debe incluir id y version.")
        return self


class ConsultaInformacionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    operation_id: UUID = Field(alias="operationId")
    socio_id: str = Field(alias="socioId", min_length=1, max_length=50)
    actor_id: UUID = Field(alias="actorId")
    cliente_id: UUID = Field(alias="clienteId")
    producto_id: str = Field(alias="productoId", min_length=1, max_length=64)
    finalidad: str = Field(min_length=1, max_length=40)
    datos_solicitados: tuple[str, ...] = Field(
        alias="datosSolicitados", min_length=1, max_length=20
    )


def create_app(
    config: Config | None = None,
    consentimientos: Consentimientos | None = None,
    fuentes: tuple[FuentePerfilamiento, ...] | None = None,
) -> Flask:
    config = config or Config.desde_entorno()
    app = Flask(__name__)
    repositorio = RepositorioPerfiles(config.database_url)
    servicio = ConsultarPerfil(
        repositorio,
        consentimientos or ConsentimientosHttp(config.identidad_url, config.internal_api_token),
        fuentes
        if fuentes is not None
        else (
            FuenteMock("open_finance", config.mock_escenario),
            FuenteMock("open_data", config.mock_escenario),
        ),
    )
    servicio_informacion = ConsultarInformacionCliente(
        repositorio,
        consentimientos or ConsentimientosHttp(config.identidad_url, config.internal_api_token),
        fuentes
        if fuentes is not None
        else (
            FuenteMock("open_finance", config.mock_escenario),
            FuenteMock("open_data", config.mock_escenario),
        ),
    )
    servicio_excepciones = GestionarExcepcionesOpenFinance(
        repositorio,
        consentimientos or ConsentimientosHttp(config.identidad_url, config.internal_api_token),
    )
    app.extensions["perfiles"] = repositorio
    app.extensions["consultar_perfil"] = servicio
    app.extensions["consultar_informacion"] = servicio_informacion
    app.extensions["excepciones_open_finance"] = servicio_excepciones

    @app.cli.command("init-db")
    def init_db() -> None:
        repositorio.inicializar()
        click.echo("Esquema inicial de Perfilamiento creado.")

    @app.before_request
    def autenticar() -> tuple[Response, int] | None:
        g.correlation_id = request.headers.get("X-Correlation-ID") or str(uuid4())
        if request.path != "/health" and not hmac.compare_digest(
            request.headers.get("Authorization", ""), f"Bearer {config.internal_api_token}"
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

    @app.post("/v1/perfiles")
    def generar() -> tuple[Response, int]:
        try:
            payload = ConsultaRequest.model_validate(request.get_json())
        except (ValidationError, BadRequest, UnsupportedMediaType):
            return jsonify(error="solicitud_invalida"), 400
        perfil = servicio.generar(
            payload.cliente_id,
            payload.referencia_id,
            g.correlation_id,
            payload.consentimiento_id,
            payload.consentimiento_version,
            payload.fuentes,
            payload.finalidad,
            payload.datos,
            payload.operation_id,
            payload.credito_id,
        )
        return jsonify(perfil.model_dump(mode="json", by_alias=True)), 201

    @app.post("/v1/consultas-informacion-cliente")
    def consultar_informacion() -> tuple[Response, int]:
        try:
            payload = ConsultaInformacionRequest.model_validate(request.get_json())
        except (ValidationError, BadRequest, UnsupportedMediaType):
            return jsonify(error="solicitud_invalida"), 400
        consulta = servicio_informacion.ejecutar(
            ConsultarInformacionComando(
                operation_id=payload.operation_id,
                socio_id=payload.socio_id,
                actor_id=payload.actor_id,
                cliente_id=payload.cliente_id,
                producto_id=payload.producto_id,
                finalidad=payload.finalidad,
                datos_solicitados=payload.datos_solicitados,
            ),
            g.correlation_id,
        )
        return jsonify(consulta.model_dump(mode="json", by_alias=True)), 201

    @app.get("/v1/consultas-informacion-cliente/<uuid:operation_id>")
    def recuperar_informacion(operation_id: UUID) -> tuple[Response, int] | Response:
        socio_id = request.args.get("socioId", "")
        if not socio_id or len(socio_id) > 50:
            return jsonify(error="solicitud_invalida"), 400
        consulta = servicio_informacion.recuperar(socio_id, operation_id, g.correlation_id)
        return jsonify(consulta.model_dump(mode="json", by_alias=True))

    @app.post("/v1/eventos/consentimientos")
    def aplicar_consentimiento() -> tuple[Response, int] | Response:
        try:
            evento = EventoConsentimiento.model_validate(request.get_json())
        except (ValidationError, BadRequest, UnsupportedMediaType):
            return jsonify(error="solicitud_invalida"), 400
        return jsonify(repositorio.aplicar_consentimiento(evento))

    @app.post("/v1/excepciones-open-finance")
    def abrir_excepcion_open_finance() -> tuple[Response, int]:
        try:
            payload = AbrirExcepcionOpenFinance.model_validate(request.get_json())
        except (ValidationError, BadRequest, UnsupportedMediaType):
            return jsonify(error="solicitud_invalida"), 400
        excepcion = servicio_excepciones.abrir(payload, g.correlation_id)
        return jsonify(excepcion.model_dump(mode="json", by_alias=True)), (
            200 if excepcion.idempotente else 201
        )

    @app.get("/v1/excepciones-open-finance")
    def listar_excepciones_open_finance() -> Response | tuple[Response, int]:
        socio_id = request.args.get("socioId", "")
        if not socio_id or len(socio_id) > 50:
            return jsonify(error="solicitud_invalida"), 400
        excepciones = servicio_excepciones.listar_pendientes(socio_id)
        return jsonify(
            excepciones=[
                excepcion.model_dump(mode="json", by_alias=True) for excepcion in excepciones
            ]
        )

    @app.get("/v1/excepciones-open-finance/operaciones/<uuid:operation_id>")
    def obtener_excepcion_por_operacion(operation_id: UUID) -> Response | tuple[Response, int]:
        socio_id = request.args.get("socioId", "")
        if not socio_id or len(socio_id) > 50:
            return jsonify(error="solicitud_invalida"), 400
        excepcion = servicio_excepciones.obtener_por_operacion(operation_id, socio_id)
        return jsonify(excepcion.model_dump(mode="json", by_alias=True))

    @app.get("/v1/excepciones-open-finance/<uuid:excepcion_id>")
    def obtener_excepcion_open_finance(excepcion_id: UUID) -> Response | tuple[Response, int]:
        socio_id = request.args.get("socioId", "")
        if not socio_id or len(socio_id) > 50:
            return jsonify(error="solicitud_invalida"), 400
        excepcion = servicio_excepciones.obtener(excepcion_id, socio_id)
        return jsonify(excepcion.model_dump(mode="json", by_alias=True))

    @app.post("/v1/excepciones-open-finance/<uuid:excepcion_id>/resolver")
    def resolver_excepcion_open_finance(excepcion_id: UUID) -> Response | tuple[Response, int]:
        socio_id = request.args.get("socioId", "")
        actor_id = request.headers.get("X-Actor-ID", "")
        try:
            actor_uuid = UUID(actor_id)
            payload = ResolverExcepcionOpenFinance.model_validate(request.get_json())
        except (ValidationError, BadRequest, UnsupportedMediaType, TypeError, ValueError):
            return jsonify(error="solicitud_invalida"), 400
        if not socio_id or len(socio_id) > 50:
            return jsonify(error="solicitud_invalida"), 400
        excepcion = servicio_excepciones.resolver(
            excepcion_id,
            socio_id,
            actor_uuid,
            payload,
            g.correlation_id,
        )
        return jsonify(excepcion.model_dump(mode="json", by_alias=True))

    @app.get("/v1/perfiles/<uuid:perfil_id>")
    def obtener(perfil_id: UUID) -> tuple[Response, int] | Response:
        try:
            cliente_id = UUID(request.args.get("clienteId", ""))
        except ValueError:
            return jsonify(error="solicitud_invalida"), 400
        perfil = servicio.obtener(perfil_id, cliente_id, g.correlation_id)
        return jsonify(perfil.model_dump(mode="json", by_alias=True))

    @app.errorhandler(ErrorPerfilamiento)
    def error_controlado(error: ErrorPerfilamiento) -> tuple[Response, int]:
        logger.warning(
            "Consulta rechazada: %s", error.codigo, extra={"correlationId": g.correlation_id}
        )
        body: dict[str, object] = {"error": error.codigo, "correlationId": g.correlation_id}
        if error.fuentes is not None:
            body["fuentes"] = error.fuentes
        return jsonify(body), error.status

    @app.errorhandler(500)
    def error_interno(error: Exception) -> tuple[Response, int]:
        logger.error(
            "Fallo de Perfilamiento", exc_info=error, extra={"correlationId": g.correlation_id}
        )
        return jsonify(error="servicio_no_disponible", correlationId=g.correlation_id), 503

    return app
