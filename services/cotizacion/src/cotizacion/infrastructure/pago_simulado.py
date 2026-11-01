from cotizacion.domain.pago import ResultadoSimuladorPago


class SimuladorPago:
    def __init__(self, resultado: str = "confirmado") -> None:
        self._resultado = ResultadoSimuladorPago(resultado)

    def evaluar(self) -> ResultadoSimuladorPago:
        return self._resultado
