from typing import Any, Protocol, cast
from urllib.parse import urlencode

import boto3  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]
from pydantic import TypeAdapter
from pydantic.networks import EmailStr

from identidad.application.password_recovery_service import RecoveryUnavailableError
from identidad.config import RecoveryDeliveryMode, validate_recovery_delivery


class SesClient(Protocol):
    def get_account(self) -> dict[str, Any]: ...

    def get_email_identity(self, **kwargs: Any) -> dict[str, Any]: ...

    def send_email(self, **kwargs: Any) -> dict[str, Any]: ...


class SesRecoveryMailSender:
    def __init__(
        self,
        region: str,
        sender: str,
        web_url: str,
        client: SesClient | None = None,
        *,
        delivery_mode: RecoveryDeliveryMode = "usuario",
        admin_mailbox: str = "",
    ) -> None:
        self._mode = validate_recovery_delivery(delivery_mode, admin_mailbox)
        self._region = region
        self._sender = str(TypeAdapter(EmailStr).validate_python(sender))
        self._admin_mailbox = (
            str(TypeAdapter(EmailStr).validate_python(admin_mailbox))
            if self._mode == "academico_administrado"
            else ""
        )
        self._web_url = web_url.rstrip("/") + "/"
        self._client = client

    def _ses(self) -> SesClient:
        if self._client is None:
            self._client = cast(SesClient, boto3.client("sesv2", region_name=self._region))
        return self._client

    def ensure_available(self) -> None:
        try:
            client = self._ses()
            account = client.get_account()
            identity = client.get_email_identity(EmailIdentity=self._sender)
            if (
                account.get("SendingEnabled") is not True
                or identity.get("VerifiedForSendingStatus") is not True
            ):
                raise RecoveryUnavailableError()
            if self._mode == "usuario":
                if account.get("ProductionAccessEnabled") is not True:
                    raise RecoveryUnavailableError()
            else:
                destination_identity = identity
                if self._admin_mailbox.casefold() != self._sender.casefold():
                    destination_identity = client.get_email_identity(
                        EmailIdentity=self._admin_mailbox
                    )
                if destination_identity.get("VerifiedForSendingStatus") is not True:
                    raise RecoveryUnavailableError()
        except (BotoCoreError, ClientError) as error:
            raise RecoveryUnavailableError() from error

    def send(self, email: str, token: str) -> None:
        link = self._web_url + "?recuperacion=1#" + urlencode({"token": token})
        academic = self._mode == "academico_administrado"
        destination = self._admin_mailbox if academic else email
        message = (
            "Recuperacion academica administrada.\n"
            f"Cuenta solicitante: {email}\n"
            "Usar este enlace autoriza cambiar solo la contrasena de esa cuenta.\n"
            if academic
            else ""
        )
        message += (
            "Abre este enlace para elegir tu nueva contrasena:\n"
            f"{link}\n\n"
            "El enlace vence en 15 minutos y solo puede usarse una vez.\n"
            "Si no solicitaste este cambio, ignora este correo."
        )
        try:
            result = self._ses().send_email(
                FromEmailAddress=self._sender,
                Destination={"ToAddresses": [destination]},
                Content={
                    "Simple": {
                        "Subject": {
                            "Data": (
                                "Recuperacion academica administrada de Solventa"
                                if academic
                                else "Recupera tu contrasena de Solventa"
                            ),
                            "Charset": "UTF-8",
                        },
                        "Body": {
                            "Text": {
                                "Data": message,
                                "Charset": "UTF-8",
                            }
                        },
                    }
                },
            )
            if not isinstance(result.get("MessageId"), str) or not result["MessageId"]:
                raise RecoveryUnavailableError()
        except (BotoCoreError, ClientError) as error:
            raise RecoveryUnavailableError() from error
