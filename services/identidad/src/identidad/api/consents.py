import hmac
from collections.abc import Callable
from datetime import datetime
from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from identidad.config import Config
from identidad.domain.consent import ConsentVersionConflictError
from identidad.infrastructure.persistence.consent_repository import SqlConsentRepository
from identidad.problems import problem_response

bp = Blueprint("consents", __name__)


class ConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    autorizado: StrictBool
    version: int = Field(ge=0, strict=True)


@bp.route("/v1/clientes/<uuid:user_id>/consentimiento-perfilamiento", methods=["GET", "PUT"])
def consent(user_id: UUID) -> Response:
    config = cast(Config, current_app.extensions["identidad_config"])
    if not config.internal_api_token:
        current_app.logger.error("Consentimientos requiere INTERNAL_API_TOKEN.")
        return problem_response(
            503,
            "servicio_no_disponible",
            "Servicio no disponible",
            "El servicio de consentimiento no esta configurado.",
        )
    if not hmac.compare_digest(
        request.headers.get("Authorization", ""), f"Bearer {config.internal_api_token}"
    ):
        return problem_response(
            401, "no_autorizado", "No autorizado", "Credencial interna invalida."
        )
    repository = cast(SqlConsentRepository, current_app.extensions["consent_repository"])
    if request.method == "GET":
        result = repository.get(user_id)
        if result is None:
            return problem_response(
                404,
                "consentimiento_ausente",
                "Consentimiento ausente",
                "Autoriza el uso de datos antes de cotizar.",
            )
    else:
        try:
            payload = ConsentRequest.model_validate(request.get_json())
        except ValidationError:
            return problem_response(
                400,
                "solicitud_invalida",
                "Solicitud invalida",
                "Indica autorizado y la version vigente.",
            )
        clock = cast(Callable[[], datetime], current_app.extensions["clock"])
        try:
            result = repository.record(
                user_id, payload.autorizado, payload.version, clock(), str(g.correlation_id)
            )
        except ConsentVersionConflictError:
            return problem_response(
                409,
                "conflicto_version",
                "Conflicto de version",
                "Actualiza el consentimiento antes de modificarlo.",
            )
    response = jsonify(result.model_dump(mode="json", by_alias=True))
    response.headers["Cache-Control"] = "no-store"
    return response
