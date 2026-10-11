class QuoteServiceUnavailableError(Exception):
    """El servicio de cotización no está disponible."""


class QuoteRequestNotFoundError(Exception):
    """La solicitud no existe o no pertenece al cliente."""


class QuoteRequestConflictError(Exception):
    """La clave de idempotencia se reutilizó con datos diferentes."""


class InsuranceProductUnavailableError(Exception):
    """El seguro no está disponible para cotizar."""
