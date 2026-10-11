from datetime import timedelta

from flask import Flask, jsonify

from cotizacion.api import cotizaciones, health
from cotizacion.application.crear_cotizacion import CrearCotizacion
from cotizacion.config import Config
from cotizacion.domain.errors import ReglaDeNegocioError
from cotizacion.infrastructure.persistence.database import crear_sesiones
from cotizacion.infrastructure.persistence.repositorio_cotizaciones import RepositorioCotizacionesSqlAlchemy
from cotizacion.infrastructure.tarificador_tasa_fija import TarificadorTasaFija


def create_app(config: Config | None = None) -> Flask:
    config = config or Config.desde_entorno()
    app = Flask(__name__)

    app.extensions["crear_cotizacion"] = CrearCotizacion(
        repositorio=RepositorioCotizacionesSqlAlchemy(crear_sesiones(config.database_url)),
        tarificador=TarificadorTasaFija(config.tasa_base),
        vigencia=timedelta(days=config.vigencia_dias),
    )

    app.register_blueprint(health.bp)
    app.register_blueprint(cotizaciones.bp)

    @app.errorhandler(ReglaDeNegocioError)
    def regla_de_negocio(error: ReglaDeNegocioError):
        return jsonify(error="regla_de_negocio", mensaje=str(error)), 422

    return app
