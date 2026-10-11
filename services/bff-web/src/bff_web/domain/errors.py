class InvalidCredentialsError(Exception):
    """Las credenciales no pudieron autenticarse."""


class UnauthorizedError(Exception):
    """La sesión no existe o ya expiró."""


class TooManyAttemptsError(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("Se superó el límite de intentos.")
        self.retry_after = retry_after
