from datetime import timedelta

from flask import Flask, jsonify

from cotizacion.api import cotizaciones, health, solicitudes
from cotizacion.application.crear_cotizacion import CrearCotizacion
from cotizacion.application.registrar_solicitud import RegistrarSolicitud
from cotizacion.config import Config
from cotizacion.domain.errors import ConflictoIdempotenciaError, ReglaDeNegocioError
from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.repositorio_cotizaciones import (
    RepositorioCotizacionesSqlAlchemy,
)
from cotizacion.infrastructure.tarificador_tasa_fija import TarificadorTasaFija


def create_app(config: Config | None = None) -> Flask:
    config = config or Config.desde_entorno()
    app = Flask(__name__)

    repositorio = RepositorioCotizacionesSqlAlchemy(crear_sesiones(config.database_url))
    tarificador = TarificadorTasaFija(config.tasa_base)
    app.extensions["repositorio_cotizaciones"] = repositorio
    app.extensions["crear_cotizacion"] = CrearCotizacion(
        repositorio=repositorio,
        tarificador=tarificador,
        vigencia=timedelta(days=config.vigencia_dias),
    )
    app.extensions["registrar_solicitud"] = RegistrarSolicitud(
        repositorio=repositorio,
        tarificador=tarificador,
        vigencia=timedelta(days=config.vigencia_dias),
    )

    app.register_blueprint(health.bp)
    app.register_blueprint(cotizaciones.bp)
    app.register_blueprint(solicitudes.bp)

    @app.errorhandler(ReglaDeNegocioError)
    def regla_de_negocio(error: ReglaDeNegocioError):
        return jsonify(error="regla_de_negocio", mensaje=str(error)), 422

    @app.errorhandler(ConflictoIdempotenciaError)
    def conflicto_idempotencia(_: ConflictoIdempotenciaError):
        return jsonify(error="conflicto_idempotencia"), 409

    return app
