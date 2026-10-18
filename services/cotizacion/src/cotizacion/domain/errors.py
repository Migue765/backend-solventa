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


class SeleccionNoEncontradaError(Exception):
    """La solicitud todavía no tiene una oferta seleccionada."""


class SuscripcionNoEncontradaError(Exception):
    """La solicitud todavía no tiene una suscripción."""
