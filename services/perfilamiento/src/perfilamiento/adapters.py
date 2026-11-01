import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from pydantic import ValidationError

from perfilamiento.domain import Consentimiento, ErrorPerfilamiento, Fuente


class ConsentimientosHttp:
    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token

    def obtener(self, cliente_id: UUID, correlation_id: str) -> Consentimiento:
        request = Request(
            f"{self._base_url}/v1/clientes/{cliente_id}/consentimiento-perfilamiento",
            headers={
                "Authorization": f"Bearer {self._token}",
                "X-Correlation-ID": correlation_id,
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=0.7) as response:
                return Consentimiento.model_validate(json.load(response))
        except HTTPError as error:
            try:
                problem = json.load(error)
            except (ValueError, OSError):
                raise ErrorPerfilamiento("identidad_no_disponible", 503) from error
            code = problem.get("code", problem.get("error")) if isinstance(problem, dict) else None
            if error.code == 404 and code == "consentimiento_ausente":
                raise ErrorPerfilamiento("consentimiento_ausente", 403) from error
            raise ErrorPerfilamiento("identidad_no_disponible", 503) from error
        except (URLError, OSError, ValueError, ValidationError) as error:
            raise ErrorPerfilamiento("identidad_no_disponible", 503) from error


class FuenteMock:
    """Fixtures sinteticas: no consultan ni representan datos financieros reales."""

    def __init__(
        self,
        nombre: Literal["open_finance", "open_data"],
        escenario: Literal["disponible", "no_disponible", "vencido"] = "disponible",
    ) -> None:
        self.nombre = nombre
        self._escenario = escenario

    def consultar(self, cliente_id: UUID, ahora: datetime) -> Fuente:
        if self._escenario == "no_disponible":
            raise ErrorPerfilamiento("fuente_no_disponible", 503)
        return Fuente(
            nombre=self.nombre,
            version="mock-1",
            consultada_en=ahora,
            vigente_hasta=ahora + timedelta(minutes=15 if self._escenario == "disponible" else -1),
            simulada=True,
            indicador_riesgo=Decimal("0.50") if self.nombre == "open_finance" else Decimal("0.25"),
            variables_disponibles=(
                ("indicador_riesgo", "saldo_promedio")
                if self.nombre == "open_finance"
                else ("indicador_riesgo", "zona_geografica")
            ),
        )
