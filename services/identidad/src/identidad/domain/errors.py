class InvalidCredentialsError(Exception):
    """Las credenciales no pudieron autenticarse."""


class InvalidRefreshTokenError(Exception):
    """El refresh token no existe, expiró, fue revocado o es de otro cliente."""


class TokenIssuanceError(Exception):
    """El firmante no pudo emitir una sesion valida."""


class EmailAlreadyRegisteredError(Exception):
    """Ya existe un usuario con ese correo."""


class TooManyAttemptsError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Se superó el límite de intentos.")
        self.retry_after = retry_after
