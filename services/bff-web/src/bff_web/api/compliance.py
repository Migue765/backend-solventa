import json
from collections.abc import Callable
from datetime import datetime
from typing import cast
from uuid import UUID

from flask import Blueprint, Response, current_app, g, jsonify, request
from pydantic import ValidationError
from werkzeug.exceptions import RequestEntityTooLarge

from bff_web.api.operational_reports import _authorize
from bff_web.application.report_ports import ReportSources
from bff_web.domain.compliance import (
    AcademicConfirmation,
    ComplianceError,
    ExchangeRequest,
    academic_report,
)
from bff_web.domain.operational_reports import (
    InvalidReportFilterError,
    ReportSourceUnavailableError,
    fingerprint,
    instant,
    parse_filters,
)
from bff_web.infrastructure.http_compliance_gateway import HttpComplianceGateway
from bff_web.problems import problem_response

bp = Blueprint("compliance", __name__)


@bp.before_request
def authorize() -> Response | None:
    request.max_content_length = 2048
    return _authorize(admin=False)


@bp.after_request
def audit(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    current_app.logger.warning(
        "cumplimiento.accion endpoint=%s status=%s actorId=%s correlationId=%s",
        request.endpoint,
        response.status_code,
        g.get("audit_actor_id"),
        g.correlation_id,
    )
    return response


@bp.post("/v1/reportes/regulatorios")
def generate() -> Response:
    AcademicConfirmation.model_validate(request.get_json())
    key = UUID(request.headers.get("Idempotency-Key", ""))
    if "corte" not in request.args:
        raise ValueError("La generacion exige un corte explicito para poder reproducirse.")
    clock = cast(Callable[[], datetime], current_app.extensions["clock"])
    filters = parse_filters({name: request.args.getlist(name) for name in request.args}, clock())
    try:
        stored, _ = _gateway().call(f"/reportes/operaciones/{key}", _actor(), str(g.correlation_id))
    except ComplianceError as error:
        if error.code != "recurso_no_encontrado" or error.status != 404:
            raise
    else:
        if stored["contenido"]["filtros"] != filters.as_json() or stored["contenido"][
            "corte"
        ] != instant(filters.cut_off):
            raise ComplianceError("idempotencia_conflictiva", 409)
        return jsonify(stored)
    sources = cast(ReportSources, current_app.extensions["report_sources"])
    snapshot = sources.fetch(filters.cut_off, str(g.correlation_id))
    content = academic_report(filters, snapshot, clock())
    result, status = _gateway().call(
        "/reportes",
        _actor(),
        str(g.correlation_id),
        body={
            "formatoVersion": content["formatoVersion"],
            "corte": content["corte"],
            "contenido": json.dumps(content, sort_keys=True, separators=(",", ":")),
            "contenidoHash": fingerprint(content),
        },
        key=key,
    )
    response = jsonify(result)
    response.status_code = status
    return response


@bp.get("/v1/reportes/regulatorios/<uuid:report_id>")
@bp.get("/v1/reportes/regulatorios/<uuid:report_id>/descarga")
def get_report(report_id: UUID) -> Response:
    _empty_query()
    download = request.path.endswith("/descarga")
    path = f"/reportes/{report_id}" + ("/descarga" if download else "")
    result, _ = _gateway().call(path, _actor(), str(g.correlation_id))
    if not download:
        return jsonify(result)
    response = Response(
        json.dumps(result["contenido"], sort_keys=True, separators=(",", ":")),
        mimetype="application/json",
    )
    response.headers["Content-Disposition"] = (
        f'attachment; filename="reporte-{report_id}-academico.json"'
    )
    response.headers["X-Content-SHA256"] = result["contenidoHash"]
    return response


@bp.post("/v1/reaseguros/intercambios")
def exchange() -> Response:
    if request.args:
        raise ValueError("El intercambio no admite parametros.")
    body = ExchangeRequest.model_validate(request.get_json())
    key = UUID(request.headers.get("Idempotency-Key", ""))
    result, status = _gateway().call(
        "/intercambios",
        _actor(),
        str(g.correlation_id),
        body=body.model_dump(mode="json"),
        key=key,
    )
    response = jsonify(result)
    response.status_code = status
    return response


@bp.get("/v1/reaseguros/intercambios/<uuid:exchange_id>")
def get_exchange(exchange_id: UUID) -> Response:
    _empty_query()
    result, _ = _gateway().call(f"/intercambios/{exchange_id}", _actor(), str(g.correlation_id))
    return jsonify(result)


@bp.errorhandler(ComplianceError)
def conflict(error: ComplianceError) -> Response:
    extensions: dict[str, object] = {}
    if error.inconsistencies:
        extensions["inconsistencias"] = error.inconsistencies
    return problem_response(
        error.status,
        error.code,
        "Operacion academica no completada",
        "Revisa el recurso, la clave idempotente y las inconsistencias antes de continuar.",
        extensions=extensions,
    )


@bp.errorhandler(ReportSourceUnavailableError)
def unavailable(error: ReportSourceUnavailableError) -> Response:
    response = problem_response(
        503,
        "consolidacion_incompleta",
        "Servicio no disponible",
        "No se confirmo la operacion; no se entrega un reporte parcial.",
        extensions={"fuente": error.source},
    )
    response.headers["Retry-After"] = "5"
    return response


@bp.errorhandler(ValueError)
@bp.errorhandler(ValidationError)
@bp.errorhandler(InvalidReportFilterError)
def invalid(_: Exception) -> Response:
    return problem_response(
        400,
        "solicitud_invalida",
        "Solicitud invalida",
        "Usa filtros validos, acepta la simulacion y envia una Idempotency-Key UUID.",
    )


@bp.errorhandler(RequestEntityTooLarge)
def too_large(_: RequestEntityTooLarge) -> Response:
    return problem_response(
        413,
        "solicitud_demasiado_grande",
        "Solicitud demasiado grande",
        "El cuerpo de la solicitud academica no puede exceder 2048 bytes.",
    )


def _gateway() -> HttpComplianceGateway:
    return cast(HttpComplianceGateway, current_app.extensions["compliance_gateway"])


def _actor() -> UUID:
    return UUID(g.audit_actor_id)


def _empty_query() -> None:
    if request.args or request.get_data():
        raise ValueError("La consulta no admite parametros ni cuerpo.")
