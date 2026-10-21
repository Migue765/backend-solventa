from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from identidad.domain.consent import (
    Consent,
    ConsentScope,
    ConsentVersionConflictError,
    InvalidConsentError,
)
from identidad.infrastructure.persistence.models import ConsentEventModel, ConsentModel


class SqlConsentRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def get(self, user_id: UUID) -> Consent | None:
        with self._sessions() as session:
            row = session.scalar(
                select(ConsentModel)
                .where(ConsentModel.cliente_id == user_id)
                .order_by(ConsentModel.version.desc())
                .limit(1)
            )
            return Consent.model_validate(row.contenido) if row else None

    def record(
        self,
        user_id: UUID,
        authorized: bool,
        version: int,
        now: datetime,
        correlation_id: str,
        scope: ConsentScope | None = None,
    ) -> Consent:
        current = self.get(user_id)
        if (current.version if current else 0) != version:
            raise ConsentVersionConflictError()
        if scope is None:
            if authorized or current is None or current.estado != "autorizado":
                raise InvalidConsentError()
            consent = current.model_copy(
                update={
                    "version": version + 1,
                    "estado": "revocado",
                    "actor_id": user_id,
                    "efectiva_en": now,
                }
            )
        else:
            scope.validate_expiry(now)
            consent = Consent(
                consentimientoId=current.consentimiento_id if current else uuid4(),
                clienteId=user_id,
                actorId=user_id,
                version=version + 1,
                estado="autorizado" if authorized else "rechazado",
                efectivaEn=now,
                **scope.model_dump(by_alias=True),
            )
        content = consent.model_dump(mode="json", by_alias=True)
        event_id = uuid4()
        event_type = {
            "autorizado": "ConsentimientoAutorizado",
            "rechazado": "ConsentimientoRechazado",
            "revocado": "ConsentimientoRevocado",
        }[consent.estado]
        try:
            with self._sessions.begin() as session:
                session.add(
                    ConsentModel(cliente_id=user_id, version=consent.version, contenido=content)
                )
                session.add(
                    ConsentEventModel(
                        evento_id=event_id,
                        tipo=event_type,
                        contenido={
                            **content,
                            "eventoId": str(event_id),
                            "tipo": event_type,
                            "correlationId": correlation_id,
                        },
                    )
                )
        except IntegrityError as error:
            latest = self.get(user_id)
            if latest is not None and latest.version > version:
                raise ConsentVersionConflictError() from error
            raise
        return consent
