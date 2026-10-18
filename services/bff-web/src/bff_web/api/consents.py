import re
from typing import cast

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from bff_web.api.security import authenticate, invalid_session_response
from bff_web.application.profiling_ports import ProfilingGateway
from bff_web.domain.errors import InvalidAccessTokenError
from bff_web.domain.profiling import ProfilingError
from bff_web.problems import problem_response

bp = Blueprint("consents", __name__, url_prefix="/v1")


class ConsentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    autorizado: StrictBool
    version: int = Field(ge=0, strict=True)


@bp.route("/consentimientos/perfilamiento", methods=["GET", "PUT"])
def profiling_consent() -> Response:
    try:
        user = authenticate().user
    except InvalidAccessTokenError:
        return invalid_session_response()

    gateway = cast(ProfilingGateway, current_app.extensions["profiling_gateway"])
    if request.method == "GET":
        result = gateway.get_consent(user.user_id, str(g.correlation_id))
    else:
        try:
            payload = ConsentPayload.model_validate(request.get_json())
        except ValidationError:
            return problem_response(
                400,
                "solicitud_invalida",
                "Solicitud invalida",
                "Indica una autorizacion explicita y la version vigente.",
            )
        result = gateway.set_consent(
            user.user_id, payload.autorizado, payload.version, str(g.correlation_id)
        )
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/solicitudes-cotizacion/<request_id>/perfilamiento")
def profiling_profile(request_id: str) -> Response:
    try:
        user = authenticate().user
    except InvalidAccessTokenError:
        return invalid_session_response()
    if not re.fullmatch(r"AQT-\d{4}-[A-F0-9]{10}", request_id):
        return problem_response(
            400, "solicitud_invalida", "Solicitud invalida", "La referencia no es valida."
        )
    gateway = cast(ProfilingGateway, current_app.extensions["profiling_gateway"])
    response = jsonify(gateway.get_profile(user.user_id, request_id, str(g.correlation_id)))
    response.headers["Cache-Control"] = "no-store"
    return response


def profiling_error(error: ProfilingError) -> Response:
    current_app.logger.warning("Perfilamiento: %s", error.code)
    details = {
        "consentimiento_ausente": "Autoriza el uso de datos antes de cotizar.",
        "consentimiento_no_vigente": "La autorizacion no esta vigente.",
        "conflicto_version": "Actualiza el consentimiento antes de modificarlo.",
        "perfil_vencido": "El perfil vencio. Crea una nueva cotizacion.",
        "perfil_no_encontrado": "Esta solicitud no tiene un perfil disponible. Crea una nueva.",
        "solicitud_no_encontrada": "La solicitud no existe o no pertenece al cliente.",
    }
    return problem_response(
        error.status,
        error.code,
        "Perfilamiento no disponible",
        details.get(error.code, "No se pudo consultar el perfilamiento."),
    )
