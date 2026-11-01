from typing import Protocol
from uuid import UUID

from bff_web.domain.client_information import ClientInformationResult


class ClientInformationGateway(Protocol):
    def consult(
        self, payload: dict[str, object], correlation_id: str
    ) -> ClientInformationResult: ...

    def recover(
        self, socio_id: str, operation_id: UUID, correlation_id: str
    ) -> ClientInformationResult: ...
