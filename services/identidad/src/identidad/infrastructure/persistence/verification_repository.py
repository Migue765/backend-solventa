from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from identidad.domain.verification import MAX_ATTEMPTS, VerificationAttempt, VerificationError
from identidad.infrastructure.persistence.models import (
    IdentityVerificationAttemptModel as Attempt,
)
from identidad.infrastructure.persistence.models import IdentityVerificationModel as Verification
from identidad.infrastructure.persistence.models import UsuarioModel


def _snapshot(row: Verification) -> dict[str, object]:
    return {
        "verificacionId": str(row.verificacion_id),
        "estado": row.estado,
        "intentos": row.intentos,
        "maxIntentos": MAX_ATTEMPTS,
        "reintentoPermitido": row.estado == "rechazado" and row.intentos < MAX_ATTEMPTS,
        "simulada": True,
        "motivo": row.motivo,
    }


class SqlVerificationRepository:
    def __init__(self, sessions: sessionmaker[Session], outcomes: tuple[str, ...]) -> None:
        self._sessions = sessions
        self._outcomes = outcomes

    @staticmethod
    def _lock(session: Session, owner: UUID) -> None:
        # Serializa tambien la primera creacion, aun sin fila de verificacion (SQLite/PostgreSQL).
        session.execute(
            update(UsuarioModel)
            .where(UsuarioModel.usuario_id == owner)
            .values(usuario_id=UsuarioModel.usuario_id)
        )

    @staticmethod
    def _get(session: Session, owner: UUID, verification_id: UUID) -> Verification:
        row = session.scalar(
            select(Verification).where(
                Verification.verificacion_id == verification_id, Verification.usuario_id == owner
            )
        )
        if row is None:
            raise VerificationError("verificacion_no_encontrada", 404)
        return row

    def start(self, owner: UUID) -> dict[str, object]:
        with self._sessions.begin() as session:
            self._lock(session, owner)
            row = session.scalar(select(Verification).where(Verification.usuario_id == owner))
            if row is None:
                row = Verification(
                    verificacion_id=uuid4(),
                    usuario_id=owner,
                    estado="pendiente",
                    intentos=0,
                    motivo=None,
                    resultados=list(self._outcomes),
                )
                session.add(row)
            return _snapshot(row)

    def get(self, owner: UUID, verification_id: UUID) -> dict[str, object]:
        with self._sessions() as session:
            return _snapshot(self._get(session, owner, verification_id))

    def submit(
        self, owner: UUID, verification_id: UUID, payload: VerificationAttempt
    ) -> dict[str, object]:
        with self._sessions.begin() as session:
            self._lock(session, owner)
            row = self._get(session, owner, verification_id)
            previous = session.get(Attempt, (verification_id, payload.attempt_id))
            request = payload.model_dump()
            if previous is not None:
                if previous.solicitud != request:
                    raise VerificationError("conflicto_idempotencia", 409)
                return previous.respuesta
            if row.estado == "aprobado" or row.intentos >= MAX_ATTEMPTS:
                raise VerificationError("transicion_invalida", 409)
            outcome = row.resultados[min(row.intentos, len(row.resultados) - 1)]
            row.intentos += 1
            row.estado = "aprobado" if outcome == "aprobado" else "rechazado"
            row.motivo = None if outcome == "aprobado" else outcome
            result = _snapshot(row)
            session.add(
                Attempt(
                    verificacion_id=verification_id,
                    intento_id=payload.attempt_id,
                    solicitud=request,
                    respuesta=result,
                )
            )
            return result
