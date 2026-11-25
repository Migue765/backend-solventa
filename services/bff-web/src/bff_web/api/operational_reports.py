import json
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

from flask import Blueprint, Response, current_app, g, jsonify, request

from bff_web.api.security import authenticate, internal_authorized, invalid_session_response
from bff_web.application.report_ports import ReportSources
from bff_web.domain.errors import InvalidAccessTokenError
from bff_web.domain.operational_reports import (
    MAX_PERIOD_DAYS,
    POLICY_STATES,
    PRODUCTS,
    InvalidReportFilterError,
    ReportFilters,
    ReportSourceUnavailableError,
    build_report,
    parse_filters,
    report_csv,
)
from bff_web.problems import problem_response

bp = Blueprint("operational_reports", __name__, url_prefix="/v1/reportes/operativos")


@bp.after_request
def audit(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    payload = response.get_json(silent=True) if response.is_json else None
    code = payload.get("code") if isinstance(payload, dict) else None
    details: dict[str, object] = {
        "event": "sol322.reporte",
        "accion": g.get("report_action", "consulta"),
        "status": response.status_code,
        "code": code if isinstance(code, str) else "ok",
        "correlationId": str(g.correlation_id),
        "endpoint": request.endpoint,
    }
    for key, name in (
        ("audit_actor_id", "actorId"),
        ("report_filters", "filtros"),
        ("report_cut_off", "corte"),
        ("report_fingerprint", "huella"),
        ("report_source", "fuente"),
    ):
        if (value := g.get(key)) is not None:
            details[name] = value
    current_app.logger.warning(json.dumps(details), extra=details)
    return response


@bp.get("")
def report() -> Response:
    g.report_action = "consulta"
    result = _consolidate()
    if isinstance(result, Response):
        return result
    return jsonify(result)


@bp.get("/descarga")
def download() -> Response:
    g.report_action = "descarga"
    if "huella" not in request.args or "corte" not in request.args:
        return _invalid("La descarga requiere el corte y la huella del reporte confirmado.")
    expected = request.args.getlist("huella")
    result = _consolidate(extra=frozenset({"huella"}))
    if isinstance(result, Response):
        return result
    if len(expected) != 1 or expected[0] != result["huella"]:
        return problem_response(
            409,
            "reporte_no_reproducible",
            "Reporte no reproducible",
            "El resultado al corte no coincide con el reporte confirmado. Consultalo de nuevo.",
        )
    response = Response(report_csv(result), mimetype="text/csv")
    response.headers["Content-Disposition"] = (
        f'attachment; filename="reporte-operativo-{result["huella"][:12]}.csv"'
    )
    response.headers["X-Reporte-Huella"] = result["huella"]
    response.headers["X-Reporte-Corte"] = result["corte"]
    return response


@bp.get("/opciones")
def options() -> Response:
    g.report_action = "opciones"
    denied = _authorize()
    if denied is not None:
        return denied
    if request.args or request.get_data():
        return _invalid("El catalogo de filtros no admite parametros.")
    return jsonify(
        productos=list(PRODUCTS),
        estados=list(POLICY_STATES),
        periodoMaximoDias=MAX_PERIOD_DAYS,
    )


def _consolidate(extra: frozenset[str] = frozenset()) -> dict[str, Any] | Response:
    denied = _authorize()
    if denied is not None:
        return denied
    if request.get_data():
        return _invalid("La consulta no admite cuerpo.")
    clock = cast(Callable[[], datetime], current_app.extensions["clock"])
    args = {key: request.args.getlist(key) for key in request.args}
    try:
        filters = parse_filters(args, clock(), extra=extra)
    except InvalidReportFilterError as error:
        return _invalid(str(error))
    _remember(filters)
    sources = cast(ReportSources, current_app.extensions["report_sources"])
    try:
        snapshot = sources.fetch(filters.cut_off, str(g.correlation_id))
        result = build_report(filters, snapshot, clock())
    except ReportSourceUnavailableError as error:
        g.report_source = error.source
        response = problem_response(
            503,
            "consolidacion_incompleta",
            "Consolidacion incompleta",
            "No fue posible consolidar todas las fuentes al corte. Intentalo nuevamente.",
            extensions={"fuente": error.source},
        )
        response.headers["Retry-After"] = "5"
        return response
    g.report_fingerprint = result["huella"]
    return result


def _authorize(*, admin: bool = True) -> Response | None:
    try:
        claims = authenticate()
    except InvalidAccessTokenError:
        return invalid_session_response()
    g.audit_actor_id = str(claims.user.user_id)
    if not internal_authorized(claims, "reportes:operativos", admin=admin):
        return problem_response(
            403,
            "alcance_insuficiente",
            "Acceso denegado",
            "Los reportes operativos requieren un usuario autorizado.",
        )
    return None


def _remember(filters: ReportFilters) -> None:
    g.report_filters = filters.as_json()
    g.report_cut_off = filters.cut_off.isoformat().replace("+00:00", "Z")


def _invalid(detail: str) -> Response:
    return problem_response(400, "solicitud_invalida", "Solicitud invalida", detail)
