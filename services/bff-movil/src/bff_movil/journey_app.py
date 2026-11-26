"""Fabrica del canal movil con las operaciones de compra compartidas del monorepo."""

import os

from bff_web.config import Config as JourneyConfig
from bff_web.mobile_journey import register_mobile_journey
from flask import Flask

from bff_movil.app import create_app as mobile_app
from bff_movil.config import Config


def create_app(config: Config | None = None, journey_config: JourneyConfig | None = None) -> Flask:
    active = config or Config.from_environment()
    if journey_config is None:
        quote_url = os.environ["QUOTE_SERVICE_URL"].strip().rstrip("/")
        if not quote_url or not active.internal_api_token or not active.policies_service_url:
            raise ValueError("El recorrido requiere Cotizacion, Polizas e INTERNAL_API_TOKEN.")
        journey_config = JourneyConfig(
            identity_url=active.identity_url,
            identity_timeout_seconds=active.identity_timeout_seconds,
            jwt_issuer=active.jwt_issuer,
            jwt_audience=active.jwt_audience,
            jwks_url=active.jwks_url,
            quote_service_url=quote_url,
            policies_service_url=active.policies_service_url,
            internal_api_token=active.internal_api_token,
        )
    app = mobile_app(active)
    register_mobile_journey(app, journey_config, entorno=os.getenv("MOVIL_ENTORNO", "real"))
    return app
