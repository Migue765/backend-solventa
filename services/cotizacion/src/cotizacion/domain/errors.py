class ReglaDeNegocioError(Exception):
    """Una operación viola una regla del dominio de Cotización."""


class ConflictoIdempotenciaError(Exception):
    """Una clave de idempotencia fue reutilizada con datos diferentes."""
