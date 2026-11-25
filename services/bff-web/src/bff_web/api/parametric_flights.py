import json
import os
from datetime import date
from http.client import HTTPException
from typing import Literal, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import UUID

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from bff_web.api.security import authenticate, internal_authorized, invalid_session_response
from bff_web.config import Config
from bff_web.domain.errors import InvalidAccessTokenError
from bff_web.problems import problem_response

bp = Blueprint("parametric_flights", __name__, url_prefix="/v1/backoffice")


class FlightEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    eventoId: UUID
    ocurridoEn: AwareDatetime
    airline: str = Field(min_length=1, max_length=80)
    flightNumber: str = Field(pattern=r"^[A-Z0-9]{2,10}$")
    flightDate: date
    estado: Literal["cancelado"]
    causa: Literal["clima"]
    simulada: Literal[True]


@bp.post("/eventos-vuelo-simulados")
def receive() -> Response:
    try:
        claims = authenticate()
    except InvalidAccessTokenError:
        return invalid_session_response()
    if not internal_authorized(claims, "siniestros:revisar"):
        return problem_response(
            403, "alcance_insuficiente", "Acceso denegado", "Se requiere un asesor autorizado."
        )
    token = os.getenv("OPENDATA_VUELOS_SIMULADO_TOKEN", "").strip()
    config = cast(Config, current_app.extensions["bff_config"])
    if not token or not config.claims_service_url:
        return _unavailable()
    request.max_content_length = 8192
    try:
        if request.args:
            raise ValueError("No se admiten parametros.")
        event = FlightEvent.model_validate(request.get_json())
    except (ValueError, ValidationError):
        return problem_response(
            422,
            "evento_vuelo_invalido",
            "Evento invalido",
            "Revisa los datos del evento de vuelo simulado.",
        )
    payload = {
        "eventos": [
            {
                "eventoId": str(event.eventoId),
                "ocurridoEn": event.ocurridoEn.isoformat(),
                "tipoEvento": "vuelo_cancelado_clima",
                "ubicacionCodigo": str(event.eventoId),
                "metrica": {"nombre": "cancelacion", "valor": 1, "unidad": "boolean"},
                "vuelo": event.model_dump(mode="json", exclude={"eventoId", "ocurridoEn"}),
            }
        ]
    }
    upstream = Request(
        config.claims_service_url + "/internal/v1/eventos-parametricos/recepcion",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "X-Fuente-ID": "opendata-vuelos-simulado",
            "X-Correlation-ID": str(g.correlation_id),
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(upstream, timeout=10) as response:
            result = json.load(response)
    except HTTPError as error:
        if error.code in {409, 422, 429}:
            return problem_response(
                error.code,
                "evento_no_aceptado",
                "Evento no aceptado",
                "El evento no cumple el contrato o esta en conflicto.",
            )
        return _unavailable()
    except (OSError, HTTPException, ValueError):
        return _unavailable()
    current_app.logger.info(
        "evento_vuelo_recibido actor=%s evento=%s correlation=%s",
        claims.user.user_id,
        event.eventoId,
        g.correlation_id,
    )
    result["simulada"] = True
    response = jsonify(result)
    response.status_code = 202
    response.headers["Cache-Control"] = "no-store"
    return response


def _unavailable() -> Response:
    current_app.logger.error("Fuente de vuelos simulada no disponible")
    return problem_response(
        503,
        "fuente_no_disponible",
        "Fuente no disponible",
        "No fue posible registrar el evento simulado.",
    )
