from typing import Protocol

from bff_web.domain.client_information import ClientInformationResult


class ClientInformationGateway(Protocol):
    def consult(
        self, payload: dict[str, object], correlation_id: str
    ) -> ClientInformationResult: ...
