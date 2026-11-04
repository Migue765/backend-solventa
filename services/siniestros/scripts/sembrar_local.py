"""Siembra siniestros de ejemplo para probar localmente HU25/HU21 (solo SQLite).

El registro real usa la API; estos casos permiten explorar estados de evaluacion
aun sin implementar. Este script no debe usarse contra bases desplegadas.

    python services/siniestros/scripts/sembrar_local.py --cliente-id <UUID>
"""

import argparse
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.models import Claim
from siniestros.repository import fingerprint
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

Cambio = tuple[str, timedelta, bool]
CASOS: list[tuple[str, timedelta, list[Cambio]]] = [
    ("asistido", timedelta(hours=3), [("reportado", timedelta(0), True)]),
    (
        "asistido",
        timedelta(days=1),
        [
            ("reportado", timedelta(0), True),
            ("en_evaluacion", timedelta(hours=1), True),
            ("en_revision", timedelta(hours=2), False),
            ("en_inspeccion", timedelta(hours=4), True),
        ],
    ),
    (
        "parametrico",
        timedelta(minutes=5),
        [
            ("reportado", timedelta(0), True),
            ("en_evaluacion", timedelta(minutes=1), True),
            ("aprobado", timedelta(minutes=2), True),
            ("pagado", timedelta(minutes=10), True),
        ],
    ),
]


def sembrar(database_url: str, cliente_id: UUID, ahora: datetime) -> list[UUID]:
    if not database_url.startswith("sqlite:"):
        raise ValueError("Solo se permite sembrar bases SQLite locales.")
    engine = create_engine(database_url)
    identificadores: list[UUID] = []
    try:
        with Session(engine) as sesion, sesion.begin():
            for indice, (tipo, antelacion, cambios) in enumerate(CASOS):
                siniestro_id = uuid4()
                reportado_en = ahora - timedelta(days=len(CASOS) - indice)
                payload = {
                    "polizaId": str(uuid4()),
                    "fechaEvento": (reportado_en - antelacion).isoformat(),
                    "tipoEvento": "Evento sintetico",
                    "descripcion": "Siniestro sintetico para exploracion local.",
                    "lugar": "Lugar de prueba",
                }
                sesion.add(
                    Claim(
                        id=str(siniestro_id),
                        owner=str(cliente_id),
                        operation=str(uuid4()),
                        fingerprint=fingerprint(payload),
                        payload=payload,
                        created=reportado_en.isoformat(),
                        tipo=tipo,
                        estado=cambios[-1][0],
                        actualizado_en=reportado_en + cambios[-1][1],
                    )
                )
                sesion.flush()
                sesion.add_all(
                    CambioEstadoModel(
                        siniestro_id=str(siniestro_id),
                        secuencia=secuencia,
                        estado=estado,
                        ocurrido_en=reportado_en + desfase,
                        visible_cliente=visible,
                    )
                    for secuencia, (estado, desfase, visible) in enumerate(cambios, start=1)
                )
                identificadores.append(siniestro_id)
    finally:
        engine.dispose()
    return identificadores


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cliente-id", type=UUID, required=True)
    parser.add_argument("--database-url", default=os.getenv("SINIESTROS_DATABASE_URL"))
    argumentos = parser.parse_args()
    if not argumentos.database_url:
        parser.error("Define SINIESTROS_DATABASE_URL o --database-url.")
    for siniestro_id, (tipo, _, cambios) in zip(
        sembrar(argumentos.database_url, argumentos.cliente_id, datetime.now(UTC)),
        CASOS,
        strict=True,
    ):
        print(f"{siniestro_id}  {tipo:<11}  {cambios[-1][0]}")


if __name__ == "__main__":
    main()
