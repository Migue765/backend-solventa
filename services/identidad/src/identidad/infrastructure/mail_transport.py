import os
from typing import Any, Protocol

from pydantic import TypeAdapter
from pydantic.networks import EmailStr


class MailClient(Protocol):
    def send_email(self, **kwargs: Any) -> dict[str, Any]: ...


def send_mail(
    client: MailClient,
    sender: str,
    original: str,
    subject: str,
    content: str,
    *,
    destination: str | None = None,
    user_id: str | None = None,
) -> str:
    """Unico transporte SES; la redireccion no cambia tokens ni destinatario logico."""
    redirect = os.getenv("EMAIL_REDIRECT_TO", "").strip()
    target = str(TypeAdapter(EmailStr).validate_python(redirect or destination or original))
    body = f"Usuario: {user_id or original}\nDestinatario original: {original}\n\n{content}"
    result = client.send_email(
        FromEmailAddress=sender,
        Destination={"ToAddresses": [target]},
        Content={
            "Simple": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Text": {"Data": body, "Charset": "UTF-8"}},
            }
        },
    )
    message_id = result.get("MessageId")
    if not isinstance(message_id, str) or not message_id:
        raise ValueError("SES no confirmo la aceptacion del correo.")
    return message_id
