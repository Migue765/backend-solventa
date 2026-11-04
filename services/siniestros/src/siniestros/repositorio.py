import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from siniestros.domain import AvisoSiniestro, ErrorSiniestro
from siniestros.models import SiniestroModel


class RepositorioSiniestros:
    def __init__(self, sesiones: sessionmaker[Session]) -> None:
        self.sesiones = sesiones

    def recuperar(
        self, cliente_id: UUID, clave: UUID, aviso: AvisoSiniestro
    ) -> dict[str, object] | None:
        with self.sesiones() as sesion:
            return self._recuperar(sesion, cliente_id, clave, aviso)

    @staticmethod
    def _recuperar(
        sesion: Session, cliente_id: UUID, clave: UUID, aviso: AvisoSiniestro
    ) -> dict[str, object] | None:
        registro = sesion.scalar(
            select(SiniestroModel).where(
                SiniestroModel.cliente_id == cliente_id,
                SiniestroModel.clave_idempotencia == clave,
            )
        )
        if registro is None:
            return None
        if registro.contenido_hash != _huella(aviso):
            raise ErrorSiniestro("conflicto_idempotencia", 409)
        return _detalle(registro)

    def registrar(
        self, cliente_id: UUID, clave: UUID, aviso: AvisoSiniestro, ahora: datetime
    ) -> tuple[dict[str, object], bool]:
        try:
            with self.sesiones.begin() as sesion:
                existente = self._recuperar(sesion, cliente_id, clave, aviso)
                if existente is not None:
                    return existente, False
                registro = SiniestroModel(
                    siniestro_id=uuid4(),
                    cliente_id=cliente_id,
                    poliza_id=aviso.poliza_id,
                    clave_idempotencia=clave,
                    contenido_hash=_huella(aviso),
                    fecha_ocurrencia=aviso.fecha_ocurrencia,
                    tipo_evento=aviso.tipo_evento,
                    descripcion=aviso.descripcion,
                    lugar=aviso.lugar,
                    reportado_en=ahora,
                    estado="registrado",
                    historial=[
                        {
                            "estado": "registrado",
                            "fecha": ahora.isoformat(),
                            "actorId": str(cliente_id),
                        }
                    ],
                )
                sesion.add(registro)
                sesion.flush()
                return _detalle(registro), True
        except IntegrityError:
            with self.sesiones() as sesion:
                existente = self._recuperar(sesion, cliente_id, clave, aviso)
                if existente is not None:
                    return existente, False
            raise

    def listar(self, cliente_id: UUID, polizas: list[UUID]) -> list[dict[str, object]]:
        with self.sesiones() as sesion:
            return [
                _resumen(registro)
                for registro in sesion.scalars(
                    select(SiniestroModel)
                    .where(
                        SiniestroModel.cliente_id == cliente_id,
                        SiniestroModel.poliza_id.in_(polizas),
                    )
                    .order_by(SiniestroModel.reportado_en.desc(), SiniestroModel.siniestro_id)
                )
            ]

    def consultar(
        self, cliente_id: UUID, siniestro_id: UUID, polizas: list[UUID]
    ) -> dict[str, object]:
        with self.sesiones() as sesion:
            registro = sesion.scalar(
                select(SiniestroModel).where(
                    SiniestroModel.siniestro_id == siniestro_id,
                    SiniestroModel.cliente_id == cliente_id,
                    SiniestroModel.poliza_id.in_(polizas),
                )
            )
            if registro is None:
                raise ErrorSiniestro("siniestro_no_encontrado", 404)
            return _detalle(registro)


def _huella(aviso: AvisoSiniestro) -> str:
    return hashlib.sha256(aviso.model_dump_json(by_alias=True).encode()).hexdigest()


def _fecha(fecha: datetime) -> str:
    # SQLite devuelve instantes sin zona; se almacenan siempre en UTC.
    return fecha.replace(tzinfo=UTC).isoformat() if fecha.tzinfo is None else fecha.isoformat()


def _resumen(registro: SiniestroModel) -> dict[str, object]:
    return {
        "id": str(registro.siniestro_id),
        "polizaId": str(registro.poliza_id),
        "fechaReporte": _fecha(registro.reportado_en),
        "estado": registro.estado,
    }


def _detalle(registro: SiniestroModel) -> dict[str, object]:
    return {
        "id": str(registro.siniestro_id),
        "polizaId": str(registro.poliza_id),
        "estado": registro.estado,
        "registradoEn": _fecha(registro.reportado_en),
        "fechaEvento": _fecha(registro.fecha_ocurrencia),
        "tipoEvento": registro.tipo_evento,
        "descripcion": registro.descripcion,
        "lugar": registro.lugar,
        "historial": registro.historial,
    }
