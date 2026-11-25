from typing import cast

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import ValidationError

from bff_web.api.policy_renewals import _client, _invalid, _not_found, _unavailable, _uuid
from bff_web.application.policy_document_ports import PolicyDocumentGateway
from bff_web.domain.policy_document import DocumentConfirmation, DocumentConflictError
from bff_web.domain.policy_renewal import PolicyNotFoundError, RenewalServiceUnavailableError
from bff_web.problems import problem_response

bp = Blueprint("policy_documents", __name__, url_prefix="/v1/clientes/me/polizas")
CONFLICTS = {
    "documento_desactualizado": "Consulta el documento vigente y acepta su contenido actualizado.",
    "idempotencia_conflictiva": "La clave de la operacion ya se uso con otra solicitud.",
}


@bp.after_request
def private(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; sandbox"
    )
    if response.status_code >= 400:
        current_app.logger.warning(
            "documentos.rechazo canal=web status=%s correlationId=%s",
            response.status_code,
            g.correlation_id,
        )
    return response


@bp.get("/<policy_id>/documento")
def get_document(policy_id: str) -> Response:
    client_id = _client()
    if isinstance(client_id, Response):
        return client_id
    policy = _uuid(policy_id)
    if policy is None or request.args or request.get_data():
        return _invalid("La consulta requiere un UUID y no admite parametros ni cuerpo.")
    try:
        document = _gateway().get_document(policy, client_id, str(g.correlation_id))
    except PolicyNotFoundError:
        return _not_found()
    except RenewalServiceUnavailableError:
        return _unavailable("No fue posible consultar el documento de la poliza.")
    return jsonify(document.model_dump(mode="json", by_alias=True))


@bp.get("/<policy_id>/documentos/<document_id>/descarga")
def download(policy_id: str, document_id: str) -> Response:
    client_id = _client()
    if isinstance(client_id, Response):
        return client_id
    policy, document = _uuid(policy_id), _uuid(document_id)
    if policy is None or document is None or request.args or request.get_data():
        return _invalid(
            "La descarga requiere UUID de poliza y documento, sin parametros ni cuerpo."
        )
    try:
        result = _gateway().download(policy, client_id, document, str(g.correlation_id))
    except PolicyNotFoundError:
        return _not_found()
    except RenewalServiceUnavailableError:
        return _unavailable("No fue posible descargar el documento; no se genero un sustituto.")
    response = Response(result.content, content_type="text/html; charset=utf-8")
    response.headers["Content-Disposition"] = (
        f'attachment; filename="poliza-{policy}-{document}-simulada.html"'
    )
    response.headers["X-Content-SHA256"] = result.content_hash
    return response


@bp.get("/<policy_id>/documentos/<document_id>/descarga/pdf")
def download_pdf(policy_id: str, document_id: str) -> Response:
    client_id = _client()
    if isinstance(client_id, Response):
        return client_id
    policy, document = _uuid(policy_id), _uuid(document_id)
    if policy is None or document is None or request.args or request.get_data():
        return _invalid(
            "La descarga requiere UUID de poliza y documento, sin parametros ni cuerpo."
        )
    try:
        result = _gateway().download_pdf(policy, client_id, document, str(g.correlation_id))
    except PolicyNotFoundError:
        return _not_found()
    except RenewalServiceUnavailableError:
        return _unavailable("No fue posible descargar el PDF; no se genero un sustituto.")
    response = Response(result.content, content_type="application/pdf")
    response.headers["Content-Disposition"] = (
        f'attachment; filename="poliza-{policy}-{document}-simulada.pdf"'
    )
    response.headers["X-Content-SHA256"] = result.content_hash
    response.headers["X-Document-Content-SHA256"] = result.document_content_hash
    return response


@bp.post("/<policy_id>/aceptaciones")
def accept(policy_id: str) -> Response:
    client_id = _client()
    if isinstance(client_id, Response):
        return client_id
    policy = _uuid(policy_id)
    key = _uuid(request.headers.get("Idempotency-Key", ""))
    if policy is None or key is None or request.args:
        return _invalid("Indica la poliza y una Idempotency-Key UUID.")
    try:
        confirmation = DocumentConfirmation.model_validate(request.get_json(silent=True))
    except ValidationError:
        return _invalid("Acepta explicitamente la simulacion e indica documento, version y hash.")
    try:
        evidence, status = _gateway().accept(
            policy, client_id, key, confirmation, str(g.correlation_id)
        )
    except PolicyNotFoundError:
        return _not_found()
    except DocumentConflictError as error:
        return problem_response(
            409, error.code, "Conflicto de aceptacion simulada", CONFLICTS[error.code]
        )
    except RenewalServiceUnavailableError:
        return _unavailable(
            "No se confirmo la aceptacion. Reintenta con la misma clave para conocer el resultado."
        )
    response = jsonify(evidence.model_dump(mode="json", by_alias=True))
    response.status_code = status
    return response


def _gateway() -> PolicyDocumentGateway:
    return cast(PolicyDocumentGateway, current_app.extensions["policy_document_gateway"])
