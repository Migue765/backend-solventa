class QuoteServiceUnavailableError(Exception):
    """El servicio de cotización no está disponible."""


class QuoteRequestNotFoundError(Exception):
    """La solicitud no existe o no pertenece al cliente."""


class QuoteRequestConflictError(Exception):
    """La clave de idempotencia se reutilizó con datos diferentes."""


class InsuranceProductUnavailableError(Exception):
    """El seguro no está disponible para cotizar."""


class InsuranceProductNotFoundError(Exception):
    """El seguro consultado no existe."""


class OfferUnavailableError(Exception):
    """La oferta no existe, cambió o ya no está vigente."""


class SimulatedPaymentNotFoundError(Exception):
    """La solicitud del cliente todavía no tiene un pago simulado."""


class PaymentNotAllowedError(Exception):
    """La suscripción o la oferta no permite confirmar el pago."""
