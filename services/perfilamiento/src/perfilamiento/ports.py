from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from perfilamiento.domain import Consentimiento, Fuente


class Consentimientos(Protocol):
    def obtener(self, cliente_id: UUID, correlation_id: str) -> Consentimiento: ...


class FuentePerfilamiento(Protocol):
    @property
    def nombre(self) -> Literal["open_finance", "open_data"]: ...

    def consultar(self, cliente_id: UUID, ahora: datetime) -> Fuente: ...
