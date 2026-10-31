class ReglaDeNegocioError(Exception):
    """Una operación viola una regla del dominio de Cotización."""


class ErrorConsultaPerfil(Exception):
    def __init__(self, codigo: str, status: int) -> None:
        super().__init__(codigo)
        self.codigo = codigo
        self.status = status


class ConflictoIdempotenciaError(Exception):
    """Una clave de idempotencia fue reutilizada con datos diferentes."""


class SolicitudNoEncontradaError(Exception):
    """La solicitud no existe o no pertenece al cliente."""


class OfertaNoDisponibleError(Exception):
    """La oferta no existe, cambió o perdió vigencia."""


class EntradaOfertaInvalidaError(Exception):
    """Una entrada de la oferta está ausente, vencida o es inconsistente; no hay oferta parcial."""

    def __init__(self, codigo: str) -> None:
        super().__init__(codigo)
        self.codigo = codigo


class OfertaSocioNoEncontradaError(Exception):
    """La operación del socio todavía no tiene una oferta persistida."""


class OperacionSocioNoEncontradaError(Exception):
    """La operación no existe dentro del socio indicado."""


class SeleccionNoEncontradaError(Exception):
    """La solicitud todavía no tiene una oferta seleccionada."""


class SuscripcionNoEncontradaError(Exception):
    """La solicitud todavía no tiene una suscripción."""


class PagoNoEncontradoError(Exception):
    """La solicitud todavía no tiene un pago simulado."""


class PagoNoPermitidoError(Exception):
    """La suscripción, selección o saga no permite confirmar un pago."""


class ServicioPolizasNoDisponibleError(Exception):
    """No se pudo emitir o consultar la poliza; el pago persistido permite reintentar."""
