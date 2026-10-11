from uuid import uuid4


def nueva_solicitud(cliente_id: str | None = None, **changes: str) -> dict[str, str]:
    payload = {
        "clienteId": cliente_id or str(uuid4()),
        "productoId": "automotive",
        "tipoCliente": "persona_natural",
        "identificacion": "12.345.678-9",
        "patenteVehiculo": "ABC123",
        "correo": "cliente@solventa.co",
        "telefono": "+56912345678",
    }
    payload.update(changes)
    return payload


def test_lista_el_catalogo_de_seguros(client) -> None:
    response = client.get("/v1/productos-seguro")

    assert response.status_code == 200
    products = response.get_json()["productos"]
    assert [product["id"] for product in products] == ["automotive", "travel", "life"]
    assert products[0]["disponible"] is True
    assert products[1]["disponible"] is False


def test_registra_y_consulta_una_solicitud(client) -> None:
    payload = nueva_solicitud()
    idempotency_key = str(uuid4())

    created = client.post(
        "/v1/solicitudes-cotizacion",
        json=payload,
        headers={"Idempotency-Key": idempotency_key},
    )

    assert created.status_code == 201
    record = created.get_json()
    assert record["id"].startswith("AQT-")
    assert record["estado"] == "recibida"
    assert record["patenteVehiculo"] == "ABC123"

    found = client.get(
        f"/v1/solicitudes-cotizacion/{record['id']}",
        query_string={"clienteId": payload["clienteId"]},
    )
    assert found.status_code == 200
    assert found.get_json() == record


def test_reintento_idempotente_devuelve_la_misma_solicitud(client) -> None:
    payload = nueva_solicitud()
    headers = {"Idempotency-Key": str(uuid4())}

    first = client.post("/v1/solicitudes-cotizacion", json=payload, headers=headers)
    second = client.post("/v1/solicitudes-cotizacion", json=payload, headers=headers)

    assert second.status_code == 201
    assert second.get_json()["id"] == first.get_json()["id"]


def test_reutilizar_la_clave_con_otros_datos_genera_conflicto(client) -> None:
    payload = nueva_solicitud()
    headers = {"Idempotency-Key": str(uuid4())}
    client.post("/v1/solicitudes-cotizacion", json=payload, headers=headers)

    response = client.post(
        "/v1/solicitudes-cotizacion",
        json={**payload, "patenteVehiculo": "XYZ987"},
        headers=headers,
    )

    assert response.status_code == 409
    assert response.get_json()["error"] == "conflicto_idempotencia"


def test_impide_cotizar_un_seguro_no_disponible(client) -> None:
    response = client.post(
        "/v1/solicitudes-cotizacion",
        json=nueva_solicitud(productoId="travel"),
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 422


def test_un_cliente_no_puede_consultar_la_solicitud_de_otro(client) -> None:
    payload = nueva_solicitud()
    created = client.post(
        "/v1/solicitudes-cotizacion",
        json=payload,
        headers={"Idempotency-Key": str(uuid4())},
    ).get_json()

    response = client.get(
        f"/v1/solicitudes-cotizacion/{created['id']}",
        query_string={"clienteId": str(uuid4())},
    )

    assert response.status_code == 404
