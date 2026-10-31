import json
from typing import cast

from flask import Response, current_app, g, request

from bff_web.application.ports import TokenVerifier
from bff_web.domain.auth import AccessClaims
from bff_web.domain.errors import InvalidAccessTokenError
from bff_web.problems import problem_response

BEARER_PREFIX = "bearer "


def audit_rejected_response(response: Response) -> Response:
    if response.status_code not in {400, 401, 403, 409, 429}:
        return response
    payload = response.get_json(silent=True)
    code = payload.get("code") if isinstance(payload, dict) else None
    if not isinstance(code, str):
        code = "solicitud_rechazada"
    details: dict[str, object] = {
        "event": "sol325.rechazo",
        "code": code,
        "reason": g.get("audit_rejection_reason", code),
        "status": response.status_code,
        "correlationId": str(g.correlation_id),
        "endpoint": request.endpoint,
    }
    if (actor_id := g.get("audit_actor_id")) is not None:
        details["actorId"] = actor_id
    if (partner_id := g.get("audit_partner_id")) is not None:
        details["socioId"] = partner_id
    current_app.logger.warning(json.dumps(details), extra=details)
    return response


def authenticate() -> AccessClaims:
    token = bearer_token()
    if token is None:
        raise InvalidAccessTokenError()
    verifier = cast(TokenVerifier, current_app.extensions["token_verifier"])
    return verifier.verify(token)


def bearer_token() -> str | None:
    header = request.headers.get("Authorization", "")
    if header.casefold().startswith(BEARER_PREFIX):
        return header[len(BEARER_PREFIX) :].strip() or None
    return None


def invalid_session_response() -> Response:
    response = problem_response(
        status=401,
        code="sesion_invalida",
        title="Sesión no válida",
        detail="Inicia sesión nuevamente.",
    )
    response.headers["WWW-Authenticate"] = "Bearer"
    return response


def partner_claims() -> AccessClaims | Response:
    try:
        claims = authenticate()
    except InvalidAccessTokenError:
        g.audit_rejection_reason = (
            "credencial_ausente" if bearer_token() is None else "credencial_invalida"
        )
        return invalid_session_response()
    if claims.user.role != "socio" or claims.scope != "socio" or not claims.user.partner_ids:
        g.audit_rejection_reason = "alcance_insuficiente"
        return problem_response(
            403, "alcance_insuficiente", "Acceso denegado", "Se requiere una cuenta de socio."
        )
    g.audit_actor_id = str(claims.user.user_id)
    return claims
