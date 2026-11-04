import json
from datetime import date
from http.client import HTTPException
from typing import Any, Literal
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from siniestros.domain import Config, Problem


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class Policy(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    tipo: str = Field(min_length=1)
    fechaInicio: date
    fechaFin: date
    estado: Literal["programada", "vigente", "vencida"]


class PolicyList(BaseModel):
    polizas: list[Policy]


def policies(
    config: Config, owner: str, correlation: str, *, only_active: bool = True
) -> list[dict[str, Any]]:
    url = f"{config.policies_url}/v1/polizas?{urlencode({'clienteId': owner})}"
    request = Request(
        url,
        headers={
            "Authorization": f"Bearer {config.internal_token}",
            "X-Correlation-ID": correlation,
        },
    )
    try:
        with build_opener(NoRedirect).open(request, timeout=5) as response:
            if response.status != 200:
                raise Problem(503, "servicio_no_disponible")
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise Problem(503, "servicio_no_disponible")
            payload = json.loads(raw)
        authorized = PolicyList.model_validate(payload).polizas
        return [
            item.model_dump(mode="json")
            for item in authorized
            if not only_active or item.estado == "vigente"
        ]
    except (OSError, HTTPException, ValueError, KeyError, TypeError) as error:
        raise Problem(503, "servicio_no_disponible") from error
