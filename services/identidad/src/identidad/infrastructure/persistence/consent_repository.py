from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from identidad.domain.consent import Consent, ConsentVersionConflictError
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
        self, user_id: UUID, authorized: bool, version: int, now: datetime, correlation_id: str
    ) -> Consent:
        current = self.get(user_id)
        if (current.version if current else 0) != version:
            raise ConsentVersionConflictError()
        consent = Consent(
            consentimientoId=current.consentimiento_id if current else uuid4(),
            clienteId=user_id,
            version=version + 1,
            estado="autorizado" if authorized else "revocado",
            efectivaEn=now,
            vigenteHasta=now + timedelta(days=30),
        )
        content = consent.model_dump(mode="json", by_alias=True)
        try:
            with self._sessions.begin() as session:
                session.add(
                    ConsentModel(cliente_id=user_id, version=consent.version, contenido=content)
                )
                session.add(
                    ConsentEventModel(
                        evento_id=uuid4(),
                        tipo="ConsentimientoAutorizado" if authorized else "ConsentimientoRevocado",
                        contenido={**content, "correlationId": correlation_id},
                    )
                )
        except IntegrityError as error:
            latest = self.get(user_id)
            if latest is not None and latest.version > version:
                raise ConsentVersionConflictError() from error
            raise
        return consent
