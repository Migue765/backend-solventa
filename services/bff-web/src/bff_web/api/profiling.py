import logging
import re
from typing import cast

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from bff_web.api.authentication import authenticated_user
from bff_web.application.profiling_ports import ProfilingGateway
from bff_web.domain.profiling import ProfilingError
from bff_web.problems import problem_response

logger = logging.getLogger(__name__)
bp = Blueprint("profiling", __name__, url_prefix="/v1")


class ConsentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    autorizado: StrictBool
    version: int = Field(ge=0, strict=True)


@bp.route("/consentimientos/perfilamiento", methods=["GET", "PUT"])
def consent() -> Response:
    user = authenticated_user()
    if user is None:
        return problem_response(
            401, "sesion_invalida", "Sesion no valida", "Inicia sesion nuevamente."
        )
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
                "Indica una autorizacion explicita y la version actual (0 si no existe).",
            )
        result = gateway.set_consent(
            user.user_id, payload.autorizado, payload.version, str(g.correlation_id)
        )
    response = jsonify(result)
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/solicitudes-cotizacion/<request_id>/perfilamiento")
def profile(request_id: str) -> Response:
    user = authenticated_user()
    if user is None:
        return problem_response(
            401, "sesion_invalida", "Sesion no valida", "Inicia sesion nuevamente."
        )
    if not re.fullmatch(r"AQT-\d{4}-[A-F0-9]{10}", request_id):
        return problem_response(
            400, "solicitud_invalida", "Solicitud invalida", "La referencia no es valida."
        )
    gateway = cast(ProfilingGateway, current_app.extensions["profiling_gateway"])
    response = jsonify(gateway.get_profile(user.user_id, request_id, str(g.correlation_id)))
    response.headers["Cache-Control"] = "no-store"
    return response


def profiling_error(error: ProfilingError) -> Response:
    logger.warning("Perfilamiento: %s", error.code, extra={"correlationId": str(g.correlation_id)})
    details = {
        "consentimiento_ausente": "Autoriza el uso de datos para cotizar antes de continuar.",
        "consentimiento_no_vigente": "El consentimiento no esta vigente. Revisa tu autorizacion.",
        "conflicto_version": "Actualiza el consentimiento antes de volver a modificarlo.",
        "perfil_vencido": "El perfil vencio. Solicita una nueva cotizacion.",
        "perfil_no_encontrado": "No hay un perfil disponible para esta solicitud.",
        "solicitud_no_encontrada": "La solicitud no existe o no pertenece al cliente.",
    }
    return problem_response(
        error.status,
        error.code,
        "Perfilamiento no disponible",
        details.get(error.code, "No fue posible consultar el perfil. Intenta nuevamente."),
    )
