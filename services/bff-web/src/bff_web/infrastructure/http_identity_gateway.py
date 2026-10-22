import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from bff_web.domain.auth import CHANNEL, TokenSet, User
from bff_web.domain.errors import IdentityRejectedError, IdentityUnavailableError

KNOWN_REJECTIONS = frozenset(
    {
        "solicitud_invalida",
        "credenciales_invalidas",
        "refresh_invalido",
        "correo_ya_registrado",
        "demasiados_intentos",
    }
)
DEFAULT_RETRY_AFTER = 60


class HttpIdentityGateway:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 5,
        client_id: str = CHANNEL,
        internal_api_token: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._client_id = client_id
        self._internal_api_token = internal_api_token

    def register(self, name: str, email: str, password: str, correlation_id: str) -> User:
        payload = self._post(
            "/v1/usuarios",
            {"nombre": name, "correo": email, "contrasena": password},
            correlation_id,
        )
        return _user_from_json(_mapping(payload, "usuario"))

    def login(self, email: str, password: str, correlation_id: str) -> TokenSet:
        payload = self._post(
            "/oauth/token",
            {
                "grant_type": "password",
                "client_id": self._client_id,
                "username": email,
                "password": password,
            },
            correlation_id,
        )
        return _token_set_from_json(payload)

    def refresh(self, refresh_token: str, correlation_id: str) -> TokenSet:
        payload = self._post(
            "/oauth/token",
            {
                "grant_type": "refresh_token",
                "client_id": self._client_id,
                "refresh_token": refresh_token,
            },
            correlation_id,
        )
        return _token_set_from_json(payload)

    def revoke(self, refresh_token: str, correlation_id: str) -> None:
        self._post("/oauth/revoke", {"refresh_token": refresh_token}, correlation_id)

    def list_clients(self, correlation_id: str) -> tuple[User, ...]:
        payload = self._get("/v1/clientes", correlation_id)
        clients = payload.get("clientes")
        if not isinstance(clients, list):
            raise IdentityUnavailableError()
        return tuple(_client_from_json(client) for client in clients)

    def _get(self, path: str, correlation_id: str) -> dict[str, Any]:
        if not self._internal_api_token:
            raise IdentityUnavailableError()
        request = Request(
            f"{self._base_url}{path}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._internal_api_token}",
                "X-Correlation-ID": correlation_id,
            },
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
                raw = response.read()
        except (HTTPError, URLError, OSError, TimeoutError) as error:
            raise IdentityUnavailableError() from error
        try:
            payload = json.loads(raw)
        except ValueError as error:
            raise IdentityUnavailableError() from error
        if not isinstance(payload, dict):
            raise IdentityUnavailableError()
        return payload

    def _post(self, path: str, body: dict[str, object], correlation_id: str) -> dict[str, Any]:
        request = Request(
            f"{self._base_url}{path}",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Correlation-ID": correlation_id,
            },
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
                raw = response.read()
        except HTTPError as error:
            raise _rejection_from(error) from error
        except (URLError, OSError, TimeoutError) as error:
            raise IdentityUnavailableError() from error

        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except ValueError as error:
            raise IdentityUnavailableError() from error
        if not isinstance(payload, dict):
            raise IdentityUnavailableError()
        return payload


def _rejection_from(error: HTTPError) -> IdentityRejectedError | IdentityUnavailableError:
    try:
        problem = json.loads(error.read())
        code = problem["code"]
    except (ValueError, KeyError, TypeError):
        return IdentityUnavailableError()
    if error.code >= 500 or code not in KNOWN_REJECTIONS:
        return IdentityUnavailableError()
    return IdentityRejectedError(error.code, str(code), _retry_after(error))


def _retry_after(error: HTTPError) -> int | None:
    if error.code != 429:
        return None
    try:
        return max(1, int(error.headers.get("Retry-After", DEFAULT_RETRY_AFTER)))
    except ValueError:
        return DEFAULT_RETRY_AFTER


def _mapping(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise IdentityUnavailableError()
    return value


def _user_from_json(data: dict[str, Any]) -> User:
    try:
        partners = data.get("socios", [])
        if not isinstance(partners, list) or not all(isinstance(value, str) for value in partners):
            raise ValueError("socios invalidos")
        return User(
            UUID(data["id"]),
            data["nombre"],
            data["correo"],
            data["rol"],
            partner_ids=tuple(partners),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise IdentityUnavailableError() from error


def _client_from_json(data: object) -> User:
    if not isinstance(data, dict):
        raise IdentityUnavailableError()
    try:
        return User(
            UUID(data["clienteId"]),
            _text(data["nombre"]),
            _text(data["correo"]),
            "cliente",
        )
    except (KeyError, TypeError, ValueError) as error:
        raise IdentityUnavailableError() from error


def _token_set_from_json(payload: dict[str, Any]) -> TokenSet:
    try:
        return TokenSet(
            access_token=_text(payload["access_token"]),
            refresh_token=_text(payload["refresh_token"]),
            expires_in=_seconds(payload["expires_in"]),
            refresh_expires_in=_seconds(payload["refresh_expires_in"]),
            user=_user_from_json(_mapping(payload, "usuario")),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise IdentityUnavailableError() from error


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("texto requerido")
    return value


def _seconds(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("segundos inválidos")
    return value
