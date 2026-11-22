from decimal import Decimal
from uuid import UUID

from polizas.domain.condiciones import Condiciones, iniciales
from polizas.infrastructure.models import PolizaModel, VersionPolizaModel
from sqlalchemy import select
from sqlalchemy.orm import Session


def ultima_version(sesion: Session, poliza_id: UUID) -> VersionPolizaModel | None:
    return sesion.scalars(
        select(VersionPolizaModel)
        .where(VersionPolizaModel.poliza_id == poliza_id)
        .order_by(VersionPolizaModel.version.desc())
        .limit(1)
    ).first()


def condiciones_vigentes(sesion: Session, poliza: PolizaModel) -> tuple[int, Condiciones]:
    """La version 1 implicita son las condiciones de emision mientras no haya endosos."""
    ultima = ultima_version(sesion, poliza.poliza_id)
    if ultima is None:
        return 1, iniciales(poliza.tipo)
    return ultima.version, Condiciones(
        ultima.plan, frozenset(ultima.coberturas), int(Decimal(ultima.prima))
    )
