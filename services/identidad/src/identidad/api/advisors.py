import hmac
from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, jsonify, request

from identidad.config import Config
from identidad.domain.auth import User
from identidad.domain.documents import normalize_document
from identidad.infrastructure.persistence.user_repository import SqlUserRepository
from identidad.problems import problem_response

bp = Blueprint("advisors", __name__, url_prefix="/v1")


@bp.get("/clientes")
def list_clients() -> Response:
    if not _authorized():
        return _unauthorized()
    repository = cast(SqlUserRepository, current_app.extensions["user_repository"])
    document_type = request.args.get("tipoDocumento")
    identification = request.args.get("numeroDocumento")
    if document_type is not None or identification is not None:
        if document_type is None or identification is None:
            return problem_response(
                400,
                "documento_incompleto",
                "Documento incompleto",
                "Tipo y numero de documento son obligatorios.",
            )
        try:
            normalized_type, normalized_number = normalize_document(document_type, identification)
        except ValueError:
            return problem_response(
                400,
                "documento_invalido",
                "Documento invalido",
                "El numero no corresponde al tipo de documento.",
            )
        client = repository.find_client_by_document(normalized_type, normalized_number)
        if client is None:
            return problem_response(
                404,
                "cliente_no_encontrado",
                "Cliente no encontrado",
                "El cliente no existe.",
            )
        response = jsonify(_client_to_json(client))
        response.headers["Cache-Control"] = "no-store"
        return response
    clients = repository.list_clients()
    response = jsonify(clientes=[_client_to_json(client) for client in clients])
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
    response = jsonify(_client_to_json(client))
    response.headers["Cache-Control"] = "no-store"
    return response


def _client_to_json(client: User) -> dict[str, object]:
    result: dict[str, object] = {
        "clienteId": str(client.user_id),
        "nombre": client.name,
        "correo": client.email,
    }
    if client.document_type is not None and client.identification is not None:
        result["tipoDocumento"] = client.document_type
        result["numeroDocumento"] = client.identification
    return result


def _authorized() -> bool:
    config = cast(Config, current_app.extensions["identidad_config"])
    return bool(config.internal_api_token) and hmac.compare_digest(
        request.headers.get("Authorization", ""), f"Bearer {config.internal_api_token}"
    )


def _unauthorized() -> Response:
    return problem_response(401, "no_autorizado", "No autorizado", "Credencial interna invalida.")
