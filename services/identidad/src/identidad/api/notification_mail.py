import hmac
import os
from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, jsonify, request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from identidad.application.password_recovery_service import RecoveryUnavailableError
from identidad.application.ports import UserRepository
from identidad.infrastructure.ses_recovery_mail import SesRecoveryMailSender
from identidad.problems import problem_response

bp = Blueprint("notification_mail", __name__, url_prefix="/internal/v1/correos")


class MailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clienteId: UUID
    notificacionId: UUID
    asunto: str = Field(min_length=1, max_length=200)
    contenido: str = Field(min_length=1, max_length=5000)


@bp.post("")
def send() -> Response:
    token = os.getenv("INTERNAL_API_TOKEN", "").strip()
    if not token or not hmac.compare_digest(
        request.headers.get("Authorization", "").encode(), f"Bearer {token}".encode()
    ):
        return problem_response(
            401, "no_autorizado", "No autorizado", "Se requiere autenticacion interna."
        )
    request.max_content_length = 8192
    try:
        payload = MailRequest.model_validate(request.get_json())
    except ValidationError:
        return problem_response(
            422, "correo_invalido", "Correo invalido", "El correo no cumple el contrato."
        )
    users = cast(UserRepository, current_app.extensions["user_repository"])
    user = users.find_by_id(payload.clienteId)
    if user is None:
        return problem_response(
            404, "usuario_no_encontrado", "Usuario no encontrado", "No existe el destinatario."
        )
    sender = current_app.extensions["notification_mail_sender"]
    if not isinstance(sender, SesRecoveryMailSender):
        return _unavailable()
    try:
        message_id = sender.send_notification(
            user.email, str(user.user_id), payload.asunto, payload.contenido
        )
    except RecoveryUnavailableError:
        return _unavailable()
    return jsonify(aceptadoProveedor=True, referencia=message_id)


def _unavailable() -> Response:
    current_app.logger.error("Transporte de correo no disponible")
    return problem_response(
        503,
        "correo_no_disponible",
        "Correo no disponible",
        "No fue posible confirmar el envio al proveedor.",
    )
