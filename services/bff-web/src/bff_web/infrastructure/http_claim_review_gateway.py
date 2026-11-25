import json
from dataclasses import dataclass
from http.client import HTTPException, HTTPResponse
from typing import Any, BinaryIO, cast
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from bff_web.domain.claim_review import (
    ClaimDecision,
    ClaimDecisions,
    ClaimReviewError,
    ReviewAssignments,
    ReviewDetail,
    ReviewInput,
    ReviewQueue,
)

MAX_JSON_BYTES = 1024 * 1024
ERRORS = {
    (404, "siniestro_no_encontrado"),
    (404, "evidencia_no_encontrada"),
    (409, "transicion_no_permitida"),
    (409, "idempotencia_conflictiva"),
}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


@dataclass(frozen=True)
class EvidenceContent:
    stream: BinaryIO
    mime: str
    size: int


class HttpClaimReviewGateway:
    def __init__(self, base_url: str, internal_token: str, timeout: float = 10) -> None:
        self._base = base_url.rstrip("/")
        self._token = internal_token
        self._timeout = timeout

    def assignments(self, queue: ReviewQueue, actor: UUID, cid: str) -> ReviewAssignments:
        payload, _ = self._json(self._request(f"/colas-revision/{queue}/asignaciones", actor, cid))
        try:
            result = ReviewAssignments.model_validate(payload)
            if result.destino != queue or any(
                item.tipo != "cola" or item.destino != queue or item.estado != "activa"
                for item in result.asignaciones
            ):
                raise ValueError("La cola recibida no corresponde a la consulta.")
            return result
        except ValueError as error:
            raise ClaimReviewError() from error

    def decisions(self, claim_id: UUID, actor: UUID, cid: str) -> ClaimDecisions:
        payload, _ = self._json(self._request(f"/siniestros/{claim_id}/decisiones", actor, cid))
        try:
            result = ClaimDecisions.model_validate(payload)
            if (
                result.siniestroId != claim_id
                or any(decision.siniestroId != claim_id for decision in result.decisiones)
                or (
                    result.asignacionActiva is not None
                    and result.asignacionActiva.siniestroId != claim_id
                )
            ):
                raise ValueError("Las decisiones no corresponden al siniestro.")
            return result
        except ValueError as error:
            raise ClaimReviewError() from error

    def detail(self, claim_id: UUID, actor: UUID, cid: str) -> ReviewDetail:
        payload, _ = self._json(self._request(f"/siniestros/{claim_id}/revision", actor, cid))
        try:
            result = ReviewDetail.model_validate(payload)
            if result.siniestro.id != claim_id or any(
                evidence.siniestroId != claim_id for evidence in result.evidencias
            ):
                raise ValueError("El detalle no corresponde al siniestro.")
            return result
        except ValueError as error:
            raise ClaimReviewError() from error

    def review(
        self, claim_id: UUID, operation: UUID, actor: UUID, data: ReviewInput, cid: str
    ) -> tuple[ClaimDecision, int]:
        outgoing = self._request(
            f"/siniestros/{claim_id}/decisiones/revision",
            actor,
            cid,
            data={**data.model_dump(), "revisorId": str(actor)},
        )
        outgoing.add_header("Idempotency-Key", str(operation))
        payload, status = self._json(outgoing, statuses={200, 201})
        try:
            result = ClaimDecision.model_validate(payload)
            if (
                result.siniestroId != claim_id
                or result.operacionId != operation
                or result.origen != "humana"
                or result.actor.tipo != "revisor"
                or result.actor.id != actor
                or result.resultado != data.resultado
                or result.motivo != data.motivo
            ):
                raise ValueError("El acuse no corresponde a la decision enviada.")
            return result, status
        except ValueError as error:
            raise ClaimReviewError() from error

    def content(self, claim_id: UUID, evidence_id: UUID, actor: UUID, cid: str) -> EvidenceContent:
        request = self._request(
            f"/siniestros/{claim_id}/evidencias/{evidence_id}/contenido", actor, cid
        )
        response = self._open(request)
        try:
            size = int(response.headers["Content-Length"])
            mime = response.headers.get_content_type()
            if (
                response.status != 200
                or size <= 0
                or mime not in {"image/jpeg", "image/png", "application/pdf", "video/mp4"}
            ):
                raise ValueError("Contenido de evidencia invalido.")
        except (ValueError, KeyError, TypeError) as error:
            response.close()
            raise ClaimReviewError() from error
        return EvidenceContent(cast(BinaryIO, response), mime, size)

    def _request(
        self, path: str, actor: UUID, cid: str, data: dict[str, object] | None = None
    ) -> Request:
        if not self._base or not self._token:
            raise ClaimReviewError()
        return Request(
            self._base + "/internal/v1" + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={
                "Authorization": "Bearer " + self._token,
                "X-Revisor-ID": str(actor),
                "X-Correlation-ID": cid,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    def _open(self, request: Request) -> HTTPResponse:
        try:
            return cast(HTTPResponse, build_opener(NoRedirect).open(request, timeout=self._timeout))
        except HTTPError as error:
            try:
                with error:
                    raw = error.read(MAX_JSON_BYTES + 1)
                payload = json.loads(raw) if len(raw) <= MAX_JSON_BYTES else None
            except (OSError, HTTPException, ValueError):
                payload = None
            code = payload.get("code") if isinstance(payload, dict) else None
            if isinstance(code, str) and (error.code, code) in ERRORS:
                raise ClaimReviewError(error.code, code) from error
            raise ClaimReviewError() from error
        except (OSError, HTTPException) as error:
            raise ClaimReviewError() from error

    def _json(
        self, request: Request, statuses: set[int] | None = None
    ) -> tuple[dict[str, Any], int]:
        try:
            with self._open(request) as response:
                raw = response.read(MAX_JSON_BYTES + 1)
                if len(raw) > MAX_JSON_BYTES or response.status not in (statuses or {200}):
                    raise ValueError("Respuesta de revision invalida.")
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise ValueError("Se requiere una respuesta JSON.")
                return payload, int(response.status)
        except (OSError, HTTPException, ValueError) as error:
            raise ClaimReviewError() from error
