import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from bff_movil.domain.auth import CHANNEL, TokenSet, User
from bff_movil.domain.errors import IdentityRejectedError, IdentityUnavailableError

KNOWN_REJECTIONS = frozenset(
    {
        "solicitud_invalida",
        "credenciales_invalidas",
        "refresh_invalido",
        "correo_ya_registrado",
        "demasiados_intentos",
        "sesion_invalida",
        "acceso_denegado",
        "credencial_ya_registrada",
    }
)
DEFAULT_RETRY_AFTER = 60


class HttpIdentityGateway:
    def __init__(self, base_url: str, timeout_seconds: float = 5, client_id: str = CHANNEL) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._client_id = client_id

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

    def _post(self, path: str, body: dict[str, object], correlation_id: str) -> dict[str, Any]:
        return self._request("POST", path, body, None, correlation_id)

    def biometric_login(self, proof: dict[str, object], correlation_id: str) -> TokenSet:
        payload = self._request(
            "POST",
            "/oauth/token",
            {
                **proof,
                "grant_type": "biometric_signature",
                "client_id": self._client_id,
            },
            None,
            correlation_id,
            expected_status=200,
        )
        return _token_set_from_json(payload)

    def biometric_request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None,
        bearer: str | None,
        correlation_id: str,
    ) -> dict[str, Any]:
        expected_status = (
            204 if method == "DELETE" else 201 if path == "/v1/biometria/enrolamientos" else 200
        )
        return self._request(method, path, body, bearer, correlation_id, expected_status)

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None,
        bearer: str | None,
        correlation_id: str,
        expected_status: int | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Correlation-ID": correlation_id,
        }
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer}"
        request = Request(
            f"{self._base_url}{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            method=method,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=self._timeout_seconds) as response:  # noqa: S310
                if expected_status is not None and response.status != expected_status:
                    raise IdentityUnavailableError()
                # urllib suppresses a 204 body, so reject its framing before reading.
                if expected_status == 204 and (
                    response.headers.get("Content-Length", "0") != "0"
                    or response.headers.get("Transfer-Encoding") is not None
                ):
                    raise IdentityUnavailableError()
                raw = response.read()
        except HTTPError as error:
            raise _rejection_from(error) from error
        except (URLError, OSError, TimeoutError) as error:
            raise IdentityUnavailableError() from error

        if expected_status == 204:
            if raw:
                raise IdentityUnavailableError()
            return {}
        if not raw:
            if expected_status is not None:
                raise IdentityUnavailableError()
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
        return User(UUID(data["id"]), data["nombre"], data["correo"], data["rol"])
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
