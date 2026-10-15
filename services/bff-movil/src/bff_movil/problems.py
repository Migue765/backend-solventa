from flask import Response, g, jsonify


def problem_response(status: int, code: str, title: str, detail: str) -> Response:
    response = jsonify(
        type=f"https://api.solventa.co/problems/{code}",
        title=title,
        status=status,
        detail=detail,
        code=code,
        correlationId=str(g.correlation_id),
    )
    response.status_code = status
    response.content_type = "application/problem+json"
    response.headers["Cache-Control"] = "no-store"
    return response
