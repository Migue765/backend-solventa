from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from cotizacion.domain.errors import ConflictoIdempotenciaError
from cotizacion.domain.pago import (
    PagoSimuladoPendiente,
    ResultadoSimuladorPago,
    huella_pago_simulado,
)
from cotizacion.infrastructure.persistence.models import (
    EfectoPagoSimuladoModel,
    IntentoPagoSimuladoModel,
)


class SimuladorPago:
    def __init__(self, sesiones: sessionmaker[Session], resultado: str) -> None:
        self._sesiones = sesiones
        self._resultado = ResultadoSimuladorPago(resultado)

    @staticmethod
    def _validar_reserva(sesion: Session, efecto_id: UUID, huella: str) -> None:
        reserva = sesion.get(IntentoPagoSimuladoModel, efecto_id)
        if reserva is None or reserva.huella != huella:
            raise ConflictoIdempotenciaError()

    def consultar(self, efecto_id: UUID, huella: str) -> ResultadoSimuladorPago | None:
        with self._sesiones() as sesion:
            self._validar_reserva(sesion, efecto_id, huella)
            efecto = sesion.get(EfectoPagoSimuladoModel, efecto_id)
            if efecto is None:
                return None
            if efecto.huella != huella:
                raise ConflictoIdempotenciaError()
            return ResultadoSimuladorPago(efecto.resultado)

    def ejecutar(
        self, efecto_id: UUID, instantanea: PagoSimuladoPendiente, huella: str
    ) -> ResultadoSimuladorPago:
        if efecto_id != instantanea.pago_id or huella != huella_pago_simulado(instantanea):
            raise ConflictoIdempotenciaError()
        # T1 tiene su propio commit: un rollback de la aplicacion no deshace el efecto.
        with self._sesiones.begin() as sesion:
            self._validar_reserva(sesion, efecto_id, huella)
            dialecto = sesion.get_bind().dialect.name
            if dialecto not in {"sqlite", "postgresql"}:
                raise ValueError("El simulador requiere SQLite o PostgreSQL.")
            insertar = sqlite_insert if dialecto == "sqlite" else pg_insert
            sesion.execute(
                insertar(EfectoPagoSimuladoModel)
                .values(
                    intento_id=efecto_id,
                    huella=huella,
                    resultado=self._resultado.value,
                    evaluado_en=datetime.now(UTC),
                )
                .on_conflict_do_nothing(index_elements=["intento_id"])
            )
            efecto = sesion.get(EfectoPagoSimuladoModel, efecto_id)
            if efecto is None or efecto.huella != huella:
                raise ConflictoIdempotenciaError()
            return ResultadoSimuladorPago(efecto.resultado)
