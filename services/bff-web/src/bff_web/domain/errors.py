class InvalidAccessTokenError(Exception):
    """El token de acceso falta, expiró o no es válido."""


class IdentityUnavailableError(Exception):
    """Identidad o su JWKS no respondieron correctamente."""


class IdentityRejectedError(Exception):
    """Identidad rechazó la operación con un error de negocio conocido."""

    def __init__(self, status: int, code: str, retry_after: int | None = None) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.retry_after = retry_after
