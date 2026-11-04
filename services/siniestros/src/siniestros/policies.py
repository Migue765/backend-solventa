import json
from datetime import date
from http.client import HTTPException
from typing import Any
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from siniestros.domain import Config, Problem


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class Policy(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: UUID
    tipo: str
    fechaInicio: date
    fechaFin: date
    estado: str


def policies(config: Config, owner: str, correlation: str) -> list[dict[str, Any]]:
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
        return [
            Policy.model_validate(item).model_dump(mode="json")
            for item in payload["polizas"]
            if item["estado"] == "vigente"
        ]
    except (OSError, HTTPException, ValueError, KeyError, TypeError) as error:
        raise Problem(503, "servicio_no_disponible") from error
