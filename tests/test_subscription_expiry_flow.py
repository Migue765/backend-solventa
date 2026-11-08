from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cotizacion.infrastructure.persistence.models import (
    CotizacionModel,
    DecisionSuscripcionModel,
    OutboxEventoModel,
    SagaEmisionModel,
    SuscripcionModel,
)
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session

from tests.test_profiling_flow import REQUESTS, Flow, flow

__all__ = ["flow"]


def aceptar(flow: Flow) -> str:
    flow.authorize()
    base = f"{REQUESTS}/{flow.create_quote()}"
    ofertas = flow.client.get(base + "/ofertas", headers=flow.headers)
    assert ofertas.status_code == 200
    oferta = ofertas.get_json()["ofertas"][0]
    seleccion = flow.client.put(
        base + "/seleccion",
        json={"ofertaId": oferta["id"], "version": oferta["version"]},
        headers={**flow.headers, "Idempotency-Key": str(uuid4())},
    )
    assert seleccion.status_code == 200
    return base


def vencer(flow: Flow) -> None:
    with Session(create_engine(flow.quote_database)) as session, session.begin():
        session.execute(
            update(CotizacionModel).values(valida_hasta=datetime.now(UTC) - timedelta(seconds=1))
        )


def test_bff_rechaza_suscripcion_vencida_sin_efectos(flow: Flow) -> None:
    base = aceptar(flow)
    vencer(flow)
    respuesta = flow.client.post(
        base + "/suscripcion",
        headers={**flow.headers, "Idempotency-Key": str(uuid4())},
    )
    assert respuesta.status_code == 422
    assert respuesta.get_json()["code"] == "oferta_no_disponible"
    assert flow.client.get(base + "/suscripcion", headers=flow.headers).status_code == 404
    with Session(create_engine(flow.quote_database)) as session:
        for modelo in (SuscripcionModel, DecisionSuscripcionModel, SagaEmisionModel):
            assert session.scalar(select(func.count()).select_from(modelo)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEventoModel)
                .where(OutboxEventoModel.tipo == "SuscripcionAprobada")
            )
            == 0
        )


def test_bff_recupera_suscripcion_despues_del_vencimiento(flow: Flow) -> None:
    base = aceptar(flow)
    clave = str(uuid4())
    original = flow.client.post(
        base + "/suscripcion", headers={**flow.headers, "Idempotency-Key": clave}
    )
    assert original.status_code == 201
    vencer(flow)
    for otra_clave in (clave, str(uuid4())):
        replay = flow.client.post(
            base + "/suscripcion", headers={**flow.headers, "Idempotency-Key": otra_clave}
        )
        assert replay.status_code == 201
        assert replay.get_json() == original.get_json()
    assert (
        flow.client.get(base + "/suscripcion", headers=flow.headers).get_json()
        == original.get_json()
    )
