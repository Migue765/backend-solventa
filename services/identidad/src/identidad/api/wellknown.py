from typing import cast

from flask import Blueprint, Response, current_app, jsonify

from identidad.config import Config
from identidad.infrastructure.jwt_issuer import RsaJwtIssuer

bp = Blueprint("wellknown", __name__, url_prefix="/.well-known")

CACHE_CONTROL = "public, max-age=300"


@bp.get("/openid-configuration")
def openid_configuration() -> Response:
    issuer = _config().jwt_issuer
    response = jsonify(
        issuer=issuer,
        jwks_uri=f"{issuer}/.well-known/jwks.json",
        token_endpoint=f"{issuer}/oauth/token",
        response_types_supported=["token"],
        subject_types_supported=["public"],
        id_token_signing_alg_values_supported=["RS256"],
        scopes_supported=["cliente"],
    )
    response.headers["Cache-Control"] = CACHE_CONTROL
    return response


@bp.get("/jwks.json")
def jwks() -> Response:
    issuer = cast(RsaJwtIssuer, current_app.extensions["jwt_issuer"])
    response = jsonify(issuer.jwks())
    response.headers["Cache-Control"] = CACHE_CONTROL
    return response


def _config() -> Config:
    return cast(Config, current_app.extensions["identidad_config"])
