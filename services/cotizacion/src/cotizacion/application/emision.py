from cotizacion.application.ports import EmisorPolizas, RepositorioEmision
from cotizacion.domain.pago import PagoSimulado


def completar_emision_academica(
    pago: PagoSimulado,
    repositorio: RepositorioEmision,
    polizas: EmisorPolizas,
    correlation_id: str,
) -> bool:
    evidencia = repositorio.evidencia_emision(pago)
    if evidencia is not None:
        poliza_id = polizas.emitir(evidencia, correlation_id)
        repositorio.marcar_emitida(pago, poliza_id)
        return True
    return False
