from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from perfilamiento.domain import Consentimiento, Fuente


class Consentimientos(Protocol):
    def obtener(self, cliente_id: UUID, correlation_id: str) -> Consentimiento: ...


class AutorizacionesSocio(Protocol):
    def validar(
        self, actor_id: UUID, socio_id: str, rol: Literal["asesor", "socio"], correlation_id: str
    ) -> None: ...


class FuentePerfilamiento(Protocol):
    @property
    def nombre(self) -> str: ...

    def consultar(self, cliente_id: UUID, ahora: datetime) -> Fuente: ...
