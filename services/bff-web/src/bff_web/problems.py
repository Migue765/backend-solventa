from flask import Response, g, jsonify


def problem_response(
    status: int,
    code: str,
    title: str,
    detail: str,
    *,
    extensions: dict[str, object] | None = None,
) -> Response:
    body: dict[str, object] = {
        "type": f"https://api.solventa.co/problems/{code}",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
        "correlationId": str(g.correlation_id),
    }
    if extensions is not None:
        if body.keys() & extensions.keys():
            raise ValueError("Las extensiones no pueden reemplazar campos del problema.")
        body.update(extensions)
    response = jsonify(body)
    response.status_code = status
    response.content_type = "application/problem+json"
    response.headers["Cache-Control"] = "no-store"
    return response
