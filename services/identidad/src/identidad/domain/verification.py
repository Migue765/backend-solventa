from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

MAX_ATTEMPTS = 3
MOCK_OUTCOMES = frozenset({"aprobado", "documento_no_valido", "prueba_vida_no_valida"})


class VerificationError(Exception):
    def __init__(self, code: str, status: int) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


class StartVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class VerificationAttempt(StartVerification):
    intentoId: str = Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
    )
    documentoRef: Literal["fixture:documento-v1", "fixture:documento-v2"]
    selfieRef: Literal["fixture:selfie-v1", "fixture:selfie-v2"]

    @property
    def attempt_id(self) -> UUID:
        return UUID(self.intentoId)
