import hmac
import logging
from datetime import timedelta
from uuid import uuid4

from flask import Flask, Response, g, jsonify, request
from werkzeug.exceptions import HTTPException

from cotizacion.api import cotizaciones, health, solicitudes
from cotizacion.application.calculadora_cotizacion import CalculadoraCotizacion
from cotizacion.application.crear_cotizacion import CrearCotizacion
from cotizacion.application.ofertas import GestionarOfertas
from cotizacion.application.ports import ProveedorPerfil
from cotizacion.application.registrar_solicitud import RegistrarSolicitud
from cotizacion.config import Config
from cotizacion.domain.errors import (
    ConflictoIdempotenciaError,
    ErrorConsultaPerfil,
    OfertaNoDisponibleError,
    ReglaDeNegocioError,
    SeleccionNoEncontradaError,
    SolicitudNoEncontradaError,
)
from cotizacion.infrastructure.perfil_http import ProveedorPerfilHttp
from cotizacion.infrastructure.perfil_riesgo_simulado import PerfilRiesgoSimulado
from cotizacion.infrastructure.perfil_simulado import ProveedorPerfilSimulado
from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.repositorio_cotizaciones import (
    RepositorioCotizacionesSqlAlchemy,
)
from cotizacion.infrastructure.persistence.repositorio_reglas import (
    ReglasConCache,
    RepositorioReglasSqlAlchemy,
)
from cotizacion.infrastructure.tarificador_por_regla import TarificadorPorRegla

logger = logging.getLogger(__name__)


def create_app(config: Config | None = None, perfiles: ProveedorPerfil | None = None) -> Flask:
    config = config or Config.desde_entorno()
    app = Flask(__name__)

    sesiones = crear_sesiones(config.database_url, config.db_pool_size, config.db_max_overflow)
    repositorio = RepositorioCotizacionesSqlAlchemy(sesiones)
    perfil_http = ProveedorPerfilHttp(config.perfilamiento_url, config.internal_api_token)
    proveedor_perfil = perfiles or (
        perfil_http
        if config.perfilamiento_habilitado
        else ProveedorPerfilSimulado(config.perfil_score_simulado)
    )
    if not config.perfilamiento_habilitado:
        logger.warning("HU11 deshabilitada: se conserva el flujo de cotizacion anterior.")
    calculadora = CalculadoraCotizacion(
        reglas=ReglasConCache(RepositorioReglasSqlAlchemy(sesiones), config.reglas_cache_segundos),
        perfiles=proveedor_perfil,
        tarificador=TarificadorPorRegla(),
        vigencia=timedelta(days=config.vigencia_dias),
    )
    app.extensions["repositorio_cotizaciones"] = repositorio
    app.extensions["crear_cotizacion"] = CrearCotizacion(repositorio, calculadora)
    app.extensions["registrar_solicitud"] = RegistrarSolicitud(
        repositorio,
        calculadora,
        generador_perfiles=perfil_http
        if config.perfilamiento_habilitado and perfiles is None
        else None,
    )
    app.extensions["proveedor_perfil"] = proveedor_perfil
    app.extensions["perfilamiento_habilitado"] = config.perfilamiento_habilitado
    app.extensions["gestionar_ofertas"] = GestionarOfertas(
        repositorio,
        repositorio,
        PerfilRiesgoSimulado(),
        perfiles=perfil_http if config.perfilamiento_habilitado and perfiles is None else None,
    )

    app.register_blueprint(health.bp)
    app.register_blueprint(cotizaciones.bp)
    app.register_blueprint(solicitudes.bp)

    @app.before_request
    def asignar_correlation_id():
        g.correlation_id = request.headers.get("X-Correlation-Id") or str(uuid4())
        if (
            request.path != "/ping"
            and (config.perfilamiento_habilitado or config.internal_api_token)
            and (
                not config.internal_api_token
                or not hmac.compare_digest(
                    request.headers.get("Authorization", ""), f"Bearer {config.internal_api_token}"
                )
            )
        ):
            return jsonify(error="no_autorizado"), 401
        return None

    @app.after_request
    def devolver_correlation_id(respuesta: Response) -> Response:
        respuesta.headers["X-Correlation-Id"] = g.get("correlation_id", "")
        respuesta.headers["Cache-Control"] = "no-store"
        return respuesta

    @app.errorhandler(ErrorConsultaPerfil)
    def error_perfil(error: ErrorConsultaPerfil):
        logger.warning("Perfilamiento: %s", error.codigo, extra={"correlationId": g.correlation_id})
        return jsonify(error=error.codigo, correlationId=g.correlation_id), error.status

    @app.errorhandler(ReglaDeNegocioError)
    def regla_de_negocio(error: ReglaDeNegocioError):
        return (
            jsonify(error="regla_de_negocio", mensaje=str(error), correlationId=g.correlation_id),
            422,
        )

    @app.errorhandler(ConflictoIdempotenciaError)
    def conflicto_idempotencia(_: ConflictoIdempotenciaError):
        return jsonify(error="conflicto_idempotencia", correlationId=g.correlation_id), 409

    @app.errorhandler(Exception)
    def error_inesperado(error: Exception):
        if isinstance(error, HTTPException):
            codigo = error.code or 500
            return jsonify(
                error=error.name.lower().replace(" ", "_"), correlationId=g.get("correlation_id")
            ), codigo
        logger.exception("Error inesperado", extra={"correlationId": g.get("correlation_id")})
        return jsonify(error="error_interno", correlationId=g.get("correlation_id")), 500

    @app.errorhandler(SolicitudNoEncontradaError)
    @app.errorhandler(SeleccionNoEncontradaError)
    def no_encontrado(_: SolicitudNoEncontradaError | SeleccionNoEncontradaError):
        return jsonify(error="no_encontrado"), 404

    @app.errorhandler(OfertaNoDisponibleError)
    def oferta_no_disponible(_: OfertaNoDisponibleError):
        return jsonify(error="oferta_no_disponible"), 422

    return app
