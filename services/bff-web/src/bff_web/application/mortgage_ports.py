from typing import Protocol

from bff_web.domain.mortgage_profiling import (
    MortgageProfilingCommand,
    MortgageProfilingResult,
)


class MortgageProfilingGateway(Protocol):
    def start(
        self, command: MortgageProfilingCommand, correlation_id: str
    ) -> MortgageProfilingResult: ...
