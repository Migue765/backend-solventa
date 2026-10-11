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


def test_lista_ofertas_estables_con_datos_para_comparar(client) -> None:
    payload = nueva_solicitud()
    solicitud = client.post(
        "/v1/solicitudes-cotizacion",
        json=payload,
        headers={"Idempotency-Key": str(uuid4())},
    ).get_json()
    url = f"/v1/solicitudes-cotizacion/{solicitud['id']}/ofertas"

    primera = client.get(url, query_string={"clienteId": payload["clienteId"]})
    segunda = client.get(url, query_string={"clienteId": payload["clienteId"]})

    assert primera.status_code == 200
    resultado = primera.get_json()
    assert resultado == segunda.get_json()
    assert len(resultado["ofertas"]) == 3
    assert [oferta["estado"] for oferta in resultado["ofertas"]] == [
        "disponible",
        "disponible",
        "vencida",
    ]
    assert resultado["ofertas"][0]["recomendada"] is True
    assert resultado["ofertas"][0]["coberturas"]
    assert resultado["ofertas"][0]["condiciones"]


def test_registra_y_reemplaza_la_seleccion(client) -> None:
    payload = nueva_solicitud()
    solicitud = client.post(
        "/v1/solicitudes-cotizacion",
        json=payload,
        headers={"Idempotency-Key": str(uuid4())},
    ).get_json()
    url = f"/v1/solicitudes-cotizacion/{solicitud['id']}"
    query = {"clienteId": payload["clienteId"]}
    ofertas = client.get(f"{url}/ofertas", query_string=query).get_json()["ofertas"]

    primera = client.put(
        f"{url}/seleccion",
        query_string=query,
        json={"ofertaId": ofertas[0]["id"], "version": ofertas[0]["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    segunda = client.put(
        f"{url}/seleccion",
        query_string=query,
        json={"ofertaId": ofertas[1]["id"], "version": ofertas[1]["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    consultada = client.get(f"{url}/seleccion", query_string=query)

    assert primera.status_code == 200
    assert segunda.status_code == 200
    assert consultada.get_json() == segunda.get_json()
    assert consultada.get_json()["ofertaId"] == ofertas[1]["id"]


def test_rechaza_una_oferta_vencida_o_con_otra_version(client) -> None:
    payload = nueva_solicitud()
    solicitud = client.post(
        "/v1/solicitudes-cotizacion",
        json=payload,
        headers={"Idempotency-Key": str(uuid4())},
    ).get_json()
    url = f"/v1/solicitudes-cotizacion/{solicitud['id']}"
    query = {"clienteId": payload["clienteId"]}
    ofertas = client.get(f"{url}/ofertas", query_string=query).get_json()["ofertas"]

    vencida = client.put(
        f"{url}/seleccion",
        query_string=query,
        json={"ofertaId": ofertas[2]["id"], "version": ofertas[2]["version"]},
        headers={"Idempotency-Key": str(uuid4())},
    )
    version_invalida = client.put(
        f"{url}/seleccion",
        query_string=query,
        json={"ofertaId": ofertas[0]["id"], "version": 99},
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert vencida.status_code == 422
    assert version_invalida.status_code == 422


def test_protege_ofertas_y_seleccion_por_cliente(client) -> None:
    payload = nueva_solicitud()
    solicitud = client.post(
        "/v1/solicitudes-cotizacion",
        json=payload,
        headers={"Idempotency-Key": str(uuid4())},
    ).get_json()
    otro_cliente = {"clienteId": str(uuid4())}
    base_url = f"/v1/solicitudes-cotizacion/{solicitud['id']}"

    assert client.get(f"{base_url}/ofertas", query_string=otro_cliente).status_code == 404
    assert (
        client.get(
            f"{base_url}/seleccion",
            query_string={"clienteId": payload["clienteId"]},
        ).status_code
        == 404
    )


def test_seleccion_es_idempotente_y_detecta_conflictos(client) -> None:
    payload = nueva_solicitud()
    solicitud = client.post(
        "/v1/solicitudes-cotizacion",
        json=payload,
        headers={"Idempotency-Key": str(uuid4())},
    ).get_json()
    url = f"/v1/solicitudes-cotizacion/{solicitud['id']}"
    query = {"clienteId": payload["clienteId"]}
    ofertas = client.get(f"{url}/ofertas", query_string=query).get_json()["ofertas"]
    headers = {"Idempotency-Key": str(uuid4())}

    primera = client.put(
        f"{url}/seleccion",
        query_string=query,
        json={"ofertaId": ofertas[0]["id"], "version": 1},
        headers=headers,
    )
    reintento = client.put(
        f"{url}/seleccion",
        query_string=query,
        json={"ofertaId": ofertas[0]["id"], "version": 1},
        headers=headers,
    )
    conflicto = client.put(
        f"{url}/seleccion",
        query_string=query,
        json={"ofertaId": ofertas[1]["id"], "version": 1},
        headers=headers,
    )

    assert reintento.get_json() == primera.get_json()
    assert conflicto.status_code == 409
