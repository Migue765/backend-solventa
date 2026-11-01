from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from threading import BoundedSemaphore, Lock
from time import monotonic
from typing import TypeVar

from perfilamiento.domain import ErrorPerfilamiento

T = TypeVar("T")


@dataclass(frozen=True)
class PoliticaConsultas:
    objetivo_segundos: float = 0.120
    timeout_segundos: float = 0.700
    journey_segundos: float = 0.450
    reserva_segundos: float = 0.050
    intentos: int = 2
    fallos_para_abrir: int = 3
    recuperacion_segundos: float = 30

    def __post_init__(self) -> None:
        if not (
            0 < self.objetivo_segundos <= 0.120
            and self.objetivo_segundos <= self.timeout_segundos <= 0.700
            and 0 < self.reserva_segundos < self.journey_segundos <= 0.450
            and 1 <= self.intentos <= 2
            and self.fallos_para_abrir >= 1
            and self.recuperacion_segundos > 0
        ):
            raise ValueError("Politica de consultas fuera del presupuesto permitido.")


class EjecutorAcotado:
    """No encola consultas: un timeout retiene su cupo hasta terminar la operacion."""

    def __init__(self, capacidad: int = 8) -> None:
        self._cupos = BoundedSemaphore(capacidad)
        self._executor = ThreadPoolExecutor(max_workers=capacidad, thread_name_prefix="fuentes")

    def ejecutar(self, operacion: Callable[[], T], timeout: float) -> T:
        if not self._cupos.acquire(blocking=False):
            raise ErrorPerfilamiento("dependencia_saturada", 503)

        def ejecutar() -> T:
            try:
                return operacion()
            finally:
                self._cupos.release()

        futuro = self._executor.submit(ejecutar)
        try:
            return futuro.result(timeout=timeout)
        except TimeoutError as error:
            raise ErrorPerfilamiento("dependencia_timeout", 503) from error


class Presupuesto:
    def __init__(self, politica: PoliticaConsultas, ejecutor: EjecutorAcotado) -> None:
        self.politica = politica
        self._ejecutor = ejecutor
        self.inicio = monotonic()
        self.limite = self.inicio + politica.journey_segundos - politica.reserva_segundos
        self.dependencias: list[dict[str, object]] = []

    @property
    def restante(self) -> float:
        return max(0, self.limite - monotonic())

    def ejecutar(self, nombre: str, operacion: Callable[[], T], reserva: float = 0) -> T:
        timeout = min(self.politica.timeout_segundos, self.restante - reserva)
        if timeout <= 0:
            raise ErrorPerfilamiento("presupuesto_agotado", 503)
        inicio = monotonic()
        resultado = "disponible"
        try:
            valor = self._ejecutor.ejecutar(operacion, timeout)
            if monotonic() - inicio >= timeout:
                raise ErrorPerfilamiento("dependencia_timeout", 503)
            return valor
        except ErrorPerfilamiento as error:
            resultado = error.codigo
            raise
        finally:
            duracion = monotonic() - inicio
            self.dependencias.append(
                {
                    "dependencia": nombre,
                    "duracionMs": round(duracion * 1000, 2),
                    "objetivoExcedido": duracion > self.politica.objetivo_segundos,
                    "resultado": resultado,
                }
            )


class CircuitBreaker:
    def __init__(self, politica: PoliticaConsultas) -> None:
        self._politica = politica
        self._lock = Lock()
        self._fallos = 0
        self._abierto_hasta = 0.0
        self._sonda = False

    def permitir(self) -> bool:
        with self._lock:
            if self._fallos < self._politica.fallos_para_abrir:
                return True
            if monotonic() < self._abierto_hasta or self._sonda:
                return False
            self._sonda = True
            return True

    def registrar(self, exitoso: bool) -> None:
        with self._lock:
            if exitoso:
                self._fallos = 0
                self._abierto_hasta = 0
            else:
                self._fallos += 1
                if self._fallos >= self._politica.fallos_para_abrir:
                    self._abierto_hasta = monotonic() + self._politica.recuperacion_segundos
            self._sonda = False
