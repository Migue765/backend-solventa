import logging
from datetime import timedelta
from uuid import uuid4

from flask import Flask, Response, g, jsonify, request
from werkzeug.exceptions import HTTPException

from cotizacion.api import cotizaciones, health, solicitudes
from cotizacion.application.calculadora_cotizacion import CalculadoraCotizacion
from cotizacion.application.crear_cotizacion import CrearCotizacion
from cotizacion.application.ofertas import GestionarOfertas
from cotizacion.application.pagos import ProcesarPagoSimulado
from cotizacion.application.registrar_solicitud import RegistrarSolicitud
from cotizacion.application.suscripciones import ProcesarSuscripcion
from cotizacion.config import Config
from cotizacion.domain.errors import (
    ConflictoIdempotenciaError,
    ErrorConsultaPerfil,
    OfertaNoDisponibleError,
    PagoNoEncontradoError,
    PagoNoPermitidoError,
    ReglaDeNegocioError,
    SeleccionNoEncontradaError,
    ServicioPolizasNoDisponibleError,
    SolicitudNoEncontradaError,
    SuscripcionNoEncontradaError,
)
from cotizacion.infrastructure.perfil_http import ProveedorPerfilHttp
from cotizacion.infrastructure.perfil_riesgo_simulado import PerfilRiesgoSimulado
from cotizacion.infrastructure.perfil_simulado import ProveedorPerfilSimulado
from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.repositorio_cotizaciones import (
    RepositorioCotizacionesSqlAlchemy,
)
from cotizacion.infrastructure.persistence.repositorio_pagos import RepositorioPagosSqlAlchemy
from cotizacion.infrastructure.persistence.repositorio_reglas import (
    ReglasConCache,
    RepositorioReglasSqlAlchemy,
)
from cotizacion.infrastructure.polizas_http import PolizasHttp
from cotizacion.infrastructure.tarificador_por_regla import TarificadorPorRegla

logger = logging.getLogger(__name__)


def create_app(config: Config | None = None) -> Flask:
    config = config or Config.desde_entorno()
    app = Flask(__name__)
    app.extensions["cotizacion_config"] = config

    sesiones = crear_sesiones(config.database_url, config.db_pool_size, config.db_max_overflow)
    repositorio = RepositorioCotizacionesSqlAlchemy(sesiones)
    perfil_http = (
        ProveedorPerfilHttp(config.perfilamiento_url, config.internal_api_token)
        if config.perfilamiento_habilitado
        else None
    )
    proveedor_perfil = perfil_http or ProveedorPerfilSimulado(config.perfil_score_simulado)
    if not config.perfilamiento_habilitado:
        logger.warning("Perfilamiento remoto deshabilitado: se usa el perfil simulado local.")
    calculadora = CalculadoraCotizacion(
        reglas=ReglasConCache(RepositorioReglasSqlAlchemy(sesiones), config.reglas_cache_segundos),
        perfiles=proveedor_perfil,
        tarificador=TarificadorPorRegla(),
        vigencia=timedelta(days=config.vigencia_dias),
    )
    app.extensions["repositorio_cotizaciones"] = repositorio
    pagos = RepositorioPagosSqlAlchemy(sesiones, emitir_polizas=bool(config.polizas_url))
    app.extensions["repositorio_pagos"] = pagos
    app.extensions["polizas"] = (
        PolizasHttp(config.polizas_url, config.internal_api_token) if config.polizas_url else None
    )
    app.extensions["procesar_pago_simulado"] = ProcesarPagoSimulado(pagos)
    app.extensions["crear_cotizacion"] = CrearCotizacion(repositorio, calculadora)
    perfiles_riesgo = PerfilRiesgoSimulado()
    app.extensions["registrar_solicitud"] = RegistrarSolicitud(
        repositorio, calculadora, perfiles_riesgo, generador_perfiles=perfil_http
    )
    app.extensions["proveedor_perfil"] = perfil_http
    app.extensions["gestionar_ofertas"] = GestionarOfertas(
        repositorio, repositorio, perfiles_riesgo, perfiles=perfil_http
    )
    app.extensions["procesar_suscripcion"] = ProcesarSuscripcion(
        repositorio,
        repositorio,
        repositorio,
        perfiles=perfil_http,
    )

    app.register_blueprint(health.bp)
    app.register_blueprint(cotizaciones.bp)
    app.register_blueprint(solicitudes.bp)

    @app.before_request
    def asignar_correlation_id() -> None:
        g.correlation_id = request.headers.get("X-Correlation-Id") or str(uuid4())

    @app.after_request
    def devolver_correlation_id(respuesta: Response) -> Response:
        respuesta.headers["X-Correlation-Id"] = g.get("correlation_id", "")
        return respuesta

    @app.errorhandler(ReglaDeNegocioError)
    def regla_de_negocio(error: ReglaDeNegocioError):
        return (
            jsonify(error="regla_de_negocio", mensaje=str(error), correlationId=g.correlation_id),
            422,
        )

    @app.errorhandler(ErrorConsultaPerfil)
    def error_perfil(error: ErrorConsultaPerfil) -> tuple[Response, int]:
        logger.warning("Perfilamiento: %s", error.codigo, extra={"correlationId": g.correlation_id})
        return jsonify(error=error.codigo, correlationId=g.correlation_id), error.status

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
    @app.errorhandler(SuscripcionNoEncontradaError)
    def no_encontrado(
        _: SolicitudNoEncontradaError | SeleccionNoEncontradaError | SuscripcionNoEncontradaError,
    ):
        return jsonify(error="no_encontrado"), 404

    @app.errorhandler(OfertaNoDisponibleError)
    def oferta_no_disponible(_: OfertaNoDisponibleError):
        return jsonify(error="oferta_no_disponible"), 422

    @app.errorhandler(PagoNoEncontradoError)
    def pago_no_encontrado(_: PagoNoEncontradoError):
        return jsonify(error="pago_no_encontrado"), 404

    @app.errorhandler(PagoNoPermitidoError)
    def pago_no_permitido(_: PagoNoPermitidoError):
        return jsonify(error="pago_no_permitido"), 422

    @app.errorhandler(ServicioPolizasNoDisponibleError)
    def polizas_no_disponible(_: ServicioPolizasNoDisponibleError) -> tuple[Response, int]:
        logger.exception(
            "Servicio de polizas no disponible", extra={"correlationId": g.correlation_id}
        )
        return jsonify(error="servicio_no_disponible", correlationId=g.correlation_id), 503

    return app
