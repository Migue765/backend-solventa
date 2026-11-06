from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from flask import Flask
from flask.testing import FlaskClient
from notificaciones.service import DeliveryResult
from sqlalchemy import text
from support import FakeDeliveryGateway, MutableClock

CLIENT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
RESOURCE_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def event(event_type: str, **extra: object) -> dict[str, object]:
    resource_type = {
        "pago_parametrico_ejecutado": "pago",
        "pago_confirmado": "pago",
        "siniestro_estado_cambiado": "siniestro",
        "poliza_emitida": "poliza",
    }[event_type]
    return {
        "eventoId": str(uuid4()),
        "tipo": event_type,
        "clienteId": str(CLIENT_ID),
        "ocurridoEn": "2026-03-10T11:00:00Z",
        "recurso": {"tipo": resource_type, "id": str(RESOURCE_ID)},
        **extra,
    }


def client_headers(auth_headers: dict[str, str]) -> dict[str, str]:
    return {**auth_headers, "X-Cliente-ID": str(CLIENT_ID)}


def test_payment_event_is_delivered_and_traceable(
    client: FlaskClient,
    app: Flask,
    gateway: FakeDeliveryGateway,
    auth_headers: dict[str, str],
) -> None:
    payload = event("pago_confirmado")

    response = client.post("/v1/eventos", json=payload, headers=auth_headers)

    assert response.status_code == 201
    assert response.get_json() == {
        "eventoId": payload["eventoId"],
        "duplicado": False,
        "notificacionesGeneradas": 1,
        "entregasConfirmadas": 1,
    }
    assert len(gateway.deliveries) == 1
    delivery = gateway.deliveries[0]
    assert delivery.channel == "in_app"
    assert str(CLIENT_ID) not in delivery.content
    assert str(RESOURCE_ID) not in delivery.content

    listed = client.get("/v1/notificaciones", headers=client_headers(auth_headers))
    assert listed.status_code == 200
    notification = listed.get_json()["notificaciones"][0]
    assert notification["tipo"] == "pagos"
    assert notification["recurso"] == {"tipo": "pago", "id": str(RESOURCE_ID)}
    with app.extensions["engine"].connect() as connection:
        origin = connection.execute(
            text("SELECT tipo, cliente_id, ocurrido_en, resultado FROM eventos_notificacion")
        ).one()
        attempt = connection.execute(
            text("SELECT resultado, error FROM intentos_entrega_notificacion")
        ).one()
    assert origin.tipo == "pago_confirmado"
    assert origin.resultado == "generado"
    assert attempt.resultado == "entregada"
    assert attempt.error is None


def test_repeated_event_is_idempotent_and_changed_replay_conflicts(
    client: FlaskClient,
    gateway: FakeDeliveryGateway,
    auth_headers: dict[str, str],
) -> None:
    payload = event("pago_parametrico_ejecutado")
    first = client.post("/v1/eventos", json=payload, headers=auth_headers)
    replay = client.post("/v1/eventos", json=payload, headers=auth_headers)
    changed = {**payload, "tipo": "pago_confirmado"}

    conflict = client.post("/v1/eventos", json=changed, headers=auth_headers)

    assert first.status_code == 201
    assert replay.status_code == 200
    assert replay.get_json()["duplicado"] is True
    assert replay.get_json()["notificacionesGeneradas"] == 1
    assert len(gateway.deliveries) == 1
    assert conflict.status_code == 409
    assert conflict.get_json()["type"].endswith("/evento_en_conflicto")


def test_preferences_select_channel_and_failed_delivery_remains_pending(
    client: FlaskClient,
    app: Flask,
    gateway: FakeDeliveryGateway,
    auth_headers: dict[str, str],
) -> None:
    headers = client_headers(auth_headers)
    preferences = {
        "preferencias": [
            {"tipo": "pagos", "canal": "in_app", "habilitada": False},
            {"tipo": "pagos", "canal": "push", "habilitada": True},
        ]
    }
    updated = client.put("/v1/notificaciones/preferencias", json=preferences, headers=headers)
    gateway.results.append(DeliveryResult(False, "proveedor_temporalmente_no_disponible"))

    received = client.post("/v1/eventos", json=event("pago_confirmado"), headers=auth_headers)

    assert updated.status_code == 200
    assert received.status_code == 201
    assert received.get_json()["entregasConfirmadas"] == 0
    assert [item.channel for item in gateway.deliveries] == ["push"]
    assert client.get("/v1/notificaciones", headers=headers).get_json() == {"notificaciones": []}
    with app.extensions["engine"].connect() as connection:
        pending = connection.execute(
            text("SELECT estado, intentos, ultimo_error FROM notificaciones")
        ).one()
    assert pending.estado == "pendiente"
    assert pending.intentos == 1
    assert pending.ultimo_error == "proveedor_temporalmente_no_disponible"


def test_policy_event_schedules_configured_expiration_and_renewal_notices(
    client: FlaskClient,
    app: Flask,
    gateway: FakeDeliveryGateway,
    auth_headers: dict[str, str],
) -> None:
    response = client.post(
        "/v1/eventos",
        json=event("poliza_emitida", fechaFin="2026-05-10"),
        headers=auth_headers,
    )

    assert response.status_code == 201
    assert response.get_json()["notificacionesGeneradas"] == 2
    assert gateway.deliveries == []
    with app.extensions["engine"].connect() as connection:
        scheduled = connection.execute(
            text("SELECT tipo, programada_en FROM notificaciones ORDER BY programada_en")
        ).all()
    assert [(row.tipo, str(row.programada_en)) for row in scheduled] == [
        ("renovaciones", "2026-04-10 00:00:00.000000"),
        ("vencimientos", "2026-05-03 00:00:00.000000"),
    ]


def test_claim_content_is_minimized_and_unknown_fields_are_rejected(
    client: FlaskClient,
    gateway: FakeDeliveryGateway,
    auth_headers: dict[str, str],
) -> None:
    payload = event(
        "siniestro_estado_cambiado",
        estado="aprobado",
        numeroCuenta="123456789",
    )

    rejected = client.post("/v1/eventos", json=payload, headers=auth_headers)
    accepted = client.post(
        "/v1/eventos",
        json=event("siniestro_estado_cambiado", estado="aprobado"),
        headers=auth_headers,
    )

    assert rejected.status_code == 422
    assert accepted.status_code == 201
    delivery = gateway.deliveries[0]
    assert "aprobado" not in delivery.content
    assert "123456789" not in delivery.content


def test_delivery_stops_after_configured_failures(
    client: FlaskClient,
    app: Flask,
    clock: MutableClock,
    gateway: FakeDeliveryGateway,
    auth_headers: dict[str, str],
) -> None:
    gateway.results.extend([DeliveryResult(False, "sin_proveedor")] * 5)
    client.post("/v1/eventos", json=event("pago_confirmado"), headers=auth_headers)
    for advance in (5, 10, 20, 40):
        clock.now += timedelta(minutes=advance)
        client.post("/v1/despachos", headers=auth_headers)

    clock.now += timedelta(days=1)
    client.post("/v1/despachos", headers=auth_headers)

    with app.extensions["engine"].connect() as connection:
        result = connection.execute(
            text("SELECT estado, intentos, ultimo_error FROM notificaciones")
        ).one()
        attempts = connection.execute(
            text("SELECT COUNT(*) FROM intentos_entrega_notificacion")
        ).scalar_one()
    assert result.estado == "fallida"
    assert result.intentos == 5
    assert result.ultimo_error == "sin_proveedor"
    assert attempts == 5
    assert len(gateway.deliveries) == 5
    assert len({delivery.notification_id for delivery in gateway.deliveries}) == 1


def test_rejects_missing_authentication_and_invalid_client(
    client: FlaskClient, auth_headers: dict[str, str]
) -> None:
    assert client.post("/v1/eventos", json=event("pago_confirmado")).status_code == 401
    response = client.get(
        "/v1/notificaciones",
        headers={**auth_headers, "X-Cliente-ID": "not-a-uuid"},
    )
    assert response.status_code == 400


def test_event_rejects_naive_timestamp(client: FlaskClient, auth_headers: dict[str, str]) -> None:
    payload: dict[str, Any] = event("pago_confirmado")
    payload["ocurridoEn"] = datetime(2026, 3, 10, 11, 0).isoformat()
    response = client.post("/v1/eventos", json=payload, headers=auth_headers)
    assert response.status_code == 422
