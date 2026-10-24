import hmac
from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, jsonify, request

from identidad.config import Config
from identidad.infrastructure.persistence.user_repository import SqlUserRepository
from identidad.problems import problem_response

bp = Blueprint("advisors", __name__, url_prefix="/v1")


@bp.get("/clientes")
def list_clients() -> Response:
    if not _authorized():
        return _unauthorized()
    repository = cast(SqlUserRepository, current_app.extensions["user_repository"])
    clients = repository.list_clients()
    response = jsonify(
        clientes=[
            {
                "clienteId": str(client.user_id),
                "nombre": client.name,
                "correo": client.email,
            }
            for client in clients
        ]
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/clientes/<uuid:client_id>")
def get_client(client_id: UUID) -> Response:
    if not _authorized():
        return _unauthorized()
    repository = cast(SqlUserRepository, current_app.extensions["user_repository"])
    client = repository.find_by_id(client_id)
    if client is None or client.role != "cliente":
        return problem_response(
            404, "cliente_no_encontrado", "Cliente no encontrado", "El cliente no existe."
        )
    response = jsonify(
        clienteId=str(client.user_id),
        nombre=client.name,
        correo=client.email,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _authorized() -> bool:
    config = cast(Config, current_app.extensions["identidad_config"])
    return bool(config.internal_api_token) and hmac.compare_digest(
        request.headers.get("Authorization", ""), f"Bearer {config.internal_api_token}"
    )


def _unauthorized() -> Response:
    return problem_response(401, "no_autorizado", "No autorizado", "Credencial interna invalida.")
