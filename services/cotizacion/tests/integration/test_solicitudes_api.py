from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from cotizacion.app import create_app
from cotizacion.config import Config
from cotizacion.domain.productos import PRODUCTOS

DATOS_PRODUCTO = {
    "automotive": {"vehiclePlate": "ABC123"},
    "travel": {
        "destinationCountryCode": "ES",
        "departureDate": "2026-11-01",
        "returnDate": "2026-11-08",
    },
    "life": {"birthDate": "1990-06-15"},
    "device-protection": {
        "deviceType": "Teléfono",
        "brandModel": "Acme Pro 12",
        "purchaseValue": "4500000",
    },
    "parametric": {
        "airline": "Aerolínea Colombiana",
        "flightNumber": "AV123",
        "flightDate": (date.today() + timedelta(days=30)).isoformat(),
    },
    "parametric-weather": {
        "location": "Medellín, Antioquia",
        "weatherEvent": "heavy-rain",
        "startDate": (date.today() + timedelta(days=1)).isoformat(),
        "endDate": (date.today() + timedelta(days=30)).isoformat(),
        "insuredAmount": "10000000",
    },
    "payment-protection": {
        "creditType": "Crédito de libre inversión",
        "monthlyPayment": "850000",
    },
    "mortgage-life": {"birthDate": "1985-02-10", "mortgageBalance": "180000000"},
}

DATOS_INVALIDOS = {
    "automotive": {"vehiclePlate": "ABC-123"},
    "travel": {
        "destinationCountryCode": "ES",
        "departureDate": "2026-11-08",
        "returnDate": "2026-11-01",
    },
    "life": {"birthDate": "1990-13-15"},
    "device-protection": {
        "deviceType": "Teléfono",
        "brandModel": "Acme Pro 12",
        "purchaseValue": "0",
    },
    "parametric": {
        "airline": "Aerolínea Colombiana",
        "flightNumber": "A",
        "flightDate": "2026-11-20",
    },
    "parametric-weather": {
        "location": "Medellín, Antioquia",
        "weatherEvent": "snow",
        "startDate": (date.today() + timedelta(days=1)).isoformat(),
        "endDate": (date.today() + timedelta(days=30)).isoformat(),
        "insuredAmount": "10000000",
    },
    "payment-protection": {
        "creditType": "Crédito de libre inversión",
        "monthlyPayment": "850000.50",
    },
    "mortgage-life": {"birthDate": "1985-02-10", "mortgageBalance": "-1"},
}

NOMBRES_OFERTA = {producto.producto_id: f"{producto.nombre} Esencial" for producto in PRODUCTOS}
NOMBRES_OFERTA["parametric-weather"] = "Clima Lluvia Intensa Integral"


def nueva_solicitud(
    cliente_id: str | None = None,
    producto_id: str = "automotive",
    datos_producto: dict[str, str] | None = None,
    **changes: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "clienteId": cliente_id or str(uuid4()),
        "productoId": producto_id,
        "tipoCliente": "persona_natural",
        "tipoDocumento": "cc",
        "identificacion": "1234567890",
        "datosProducto": (
            DATOS_PRODUCTO[producto_id] if datos_producto is None else datos_producto
        ),
        "correo": "cliente@solventa.co",
        "telefono": "+573001234567",
    }
    payload.update(changes)
    return payload


def test_lista_el_catalogo_de_seguros(client) -> None:
    response = client.get("/v1/productos-seguro")

    assert response.status_code == 200
    products = response.get_json()["productos"]
    assert [product["id"] for product in products] == [
        "automotive",
        "travel",
        "life",
        "device-protection",
        "parametric",
        "parametric-weather",
        "payment-protection",
        "mortgage-life",
    ]
    assert all(product["disponible"] is True for product in products)
    assert all("simulad" not in str(product).casefold() for product in products)
    assert all(producto.suma_asegurada > 0 for producto in PRODUCTOS)
    assert all(producto.deducible >= 0 for producto in PRODUCTOS)


def test_lista_destinos_de_viaje_soportados(client) -> None:
    response = client.get("/v1/destinos-viaje")

    assert response.status_code == 200
    destinations = response.get_json()["destinos"]
    assert {"codigo": "CO", "nombre": "Colombia"} in destinations
    assert {"codigo": "ES", "nombre": "España"} in destinations
    assert destinations == sorted(destinations, key=lambda destination: destination["nombre"])


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
    assert record["datosProducto"] == {"vehiclePlate": "ABC123"}

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
        json={**payload, "datosProducto": {"vehiclePlate": "XYZ987"}},
        headers=headers,
    )

    assert response.status_code == 409
    assert response.get_json()["error"] == "conflicto_idempotencia"


@pytest.mark.parametrize("producto_id", DATOS_PRODUCTO)
def test_registra_todos_los_productos_disponibles(client, producto_id: str) -> None:
    payload = nueva_solicitud(producto_id=producto_id)
    response = client.post(
        "/v1/solicitudes-cotizacion",
        json=payload,
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 201
    assert response.get_json()["productoId"] == producto_id
    assert response.get_json()["datosProducto"] == DATOS_PRODUCTO[producto_id]


@pytest.mark.parametrize(("producto_id", "datos_producto"), DATOS_INVALIDOS.items())
def test_rechaza_datos_invalidos_para_cada_producto(
    client, producto_id: str, datos_producto: dict[str, str]
) -> None:
    response = client.post(
        "/v1/solicitudes-cotizacion",
        json=nueva_solicitud(producto_id=producto_id, datos_producto=datos_producto),
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 400


def test_rechaza_un_destino_de_viaje_fuera_del_catalogo(client) -> None:
    datos_producto = {
        **DATOS_PRODUCTO["travel"],
        "destinationCountryCode": "ZZ",
    }
    response = client.post(
        "/v1/solicitudes-cotizacion",
        json=nueva_solicitud(producto_id="travel", datos_producto=datos_producto),
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    "datos_producto",
    [{"vehiclePlate": "ABC123", "extra": "no"}, {}],
)
def test_rechaza_claves_extra_y_datos_faltantes(client, datos_producto: dict[str, str]) -> None:
    response = client.post(
        "/v1/solicitudes-cotizacion",
        json=nueva_solicitud(datos_producto=datos_producto),
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 400


def test_rechaza_un_telefono_fuera_del_mercado_colombiano(client) -> None:
    response = client.post(
        "/v1/solicitudes-cotizacion",
        json=nueva_solicitud(telefono="+56912345678"),
        headers={"Idempotency-Key": str(uuid4())},
    )

    assert response.status_code == 400


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
        "disponible",
    ]
    assert resultado["ofertas"][0]["recomendada"] is True
    assert resultado["ofertas"][0]["primaMensual"] == {
        "monto": "41250.00",
        "moneda": "COP",
    }
    assert resultado["ofertas"][0]["coberturas"]
    assert resultado["ofertas"][0]["condiciones"]


def test_identificacion_no_se_convierte_en_un_score_de_riesgo(client) -> None:
    resultados = {}
    for identificacion in ("1000000000", "2000000000", "3000000000"):
        payload = nueva_solicitud(identificacion=identificacion)
        solicitud = client.post(
            "/v1/solicitudes-cotizacion",
            json=payload,
            headers={"Idempotency-Key": str(uuid4())},
        ).get_json()
        resultados[identificacion] = client.get(
            f"/v1/solicitudes-cotizacion/{solicitud['id']}/ofertas",
            query_string={"clienteId": payload["clienteId"]},
        ).get_json()

    assert len({resultado["tasaAplicada"] for resultado in resultados.values()}) == 1
    assert next(iter(resultados.values()))["tasaAplicada"] == "Tasa 0.001500"
    assert {
        resultado["ofertas"][0]["primaMensual"]["monto"] for resultado in resultados.values()
    } == {"41250.00"}


@pytest.mark.parametrize(("producto_id", "nombre"), NOMBRES_OFERTA.items())
def test_genera_ofertas_especificas_para_cada_producto(
    client, producto_id: str, nombre: str
) -> None:
    payload = nueva_solicitud(producto_id=producto_id)
    solicitud = client.post(
        "/v1/solicitudes-cotizacion",
        json=payload,
        headers={"Idempotency-Key": str(uuid4())},
    ).get_json()

    resultado = client.get(
        f"/v1/solicitudes-cotizacion/{solicitud['id']}/ofertas",
        query_string={"clienteId": payload["clienteId"]},
    ).get_json()

    assert resultado["ofertas"][0]["nombre"] == nombre
    assert [oferta["estado"] for oferta in resultado["ofertas"]] == [
        "disponible",
        "disponible",
        "disponible",
    ]
    assert resultado["ofertas"][0]["recomendada"] is True
    if producto_id != "automotive":
        assert all(oferta["responsabilidadCivil"] == "No aplica" for oferta in resultado["ofertas"])


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

    client.application.extensions["gestionar_ofertas"]._reloj = lambda: datetime(
        2100, 1, 1, tzinfo=UTC
    )
    vencida = client.put(
        f"{url}/seleccion",
        query_string=query,
        json={"ofertaId": ofertas[0]["id"], "version": ofertas[0]["version"]},
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


VARIANTES = [
    *((producto, datos) for producto, datos in DATOS_PRODUCTO.items()),
    *(
        ("parametric", {**DATOS_PRODUCTO["parametric"], "coverageType": cobertura})
        for cobertura in ("delay-cancellation", "missed-connection", "comprehensive")
    ),
    ("parametric-weather", {**DATOS_PRODUCTO["parametric-weather"], "weatherEvent": "drought"}),
]


@pytest.mark.parametrize(("producto_id", "datos"), VARIANTES)
def test_ciclo_multirramo_persistido_y_autorizado(client, database_url, producto_id, datos) -> None:
    payload = nueva_solicitud(producto_id=producto_id, datos_producto=datos)
    headers = {"Idempotency-Key": str(uuid4())}
    respuesta = client.post("/v1/solicitudes-cotizacion", json=payload, headers=headers)
    assert respuesta.status_code == 201
    registro = respuesta.get_json()
    assert registro["datosProducto"] == datos
    assert set(registro) == {
        "id",
        "productoId",
        "tipoCliente",
        "tipoDocumento",
        "identificacion",
        "datosProducto",
        "correo",
        "telefono",
        "estado",
        "creadaEn",
        "cotizacion",
    }
    repetida = client.post("/v1/solicitudes-cotizacion", json=payload, headers=headers)
    assert repetida.get_json() == registro
    conflicto = client.post(
        "/v1/solicitudes-cotizacion",
        json={**payload, "telefono": "+573009876543"},
        headers=headers,
    )
    assert conflicto.status_code == 409

    url = f"/v1/solicitudes-cotizacion/{registro['id']}"
    query = {"clienteId": payload["clienteId"]}
    ofertas = client.get(f"{url}/ofertas", query_string=query).get_json()
    oferta = ofertas["ofertas"][0]
    assert all(item["prima"]["moneda"] == "COP" for item in ofertas["ofertas"])
    assert all(Decimal(item["prima"]["monto"]) > 0 for item in ofertas["ofertas"])
    periodicidad = {
        "travel": "por_viaje",
        "parametric": "por_vuelo",
        "parametric-weather": "por_periodo",
    }.get(producto_id, "mensual")
    assert all(item["periodicidad"] == periodicidad for item in ofertas["ofertas"])
    assert all(
        item["primaMensual"] == (item["prima"] if periodicidad == "mensual" else None)
        for item in ofertas["ofertas"]
    )
    texto_publico = str(ofertas).casefold()
    assert all(
        aviso not in texto_publico
        for aviso in (
            "simulad",
            "demostración",
            "pendiente de implementar",
            "trabajo en proceso",
            "no hay consulta",
            "sin monitoreo",
            "pagos reales",
            "oferta fuera de vigencia",
        )
    )
    body = {"ofertaId": oferta["id"], "version": oferta["version"]}
    seleccion_headers = {"Idempotency-Key": str(uuid4())}
    seleccion = client.put(
        f"{url}/seleccion", query_string=query, json=body, headers=seleccion_headers
    )
    assert seleccion.status_code == 200
    assert seleccion.get_json()["prima"] == oferta["prima"]
    assert seleccion.get_json()["periodicidad"] == periodicidad
    assert seleccion.get_json()["primaMensual"] == oferta["primaMensual"]

    otro_cliente = {"clienteId": str(uuid4())}
    for sufijo in ("", "/ofertas", "/seleccion"):
        assert client.get(f"{url}{sufijo}", query_string=otro_cliente).status_code == 404
    assert (
        client.put(
            f"{url}/seleccion", query_string=otro_cliente, json=body, headers=seleccion_headers
        ).status_code
        == 404
    )

    reiniciado = create_app(Config(database_url=database_url, vigencia_dias=15)).test_client()
    assert reiniciado.get(url, query_string=query).get_json() == registro
    assert reiniciado.get(f"{url}/ofertas", query_string=query).get_json() == ofertas
    assert reiniciado.get(f"{url}/seleccion", query_string=query).get_json() == seleccion.get_json()
    assert (
        reiniciado.put(
            f"{url}/seleccion", query_string=query, json=body, headers=seleccion_headers
        ).get_json()
        == seleccion.get_json()
    )
    if producto_id == "parametric-weather":
        repositorio = reiniciado.application.extensions["repositorio_cotizaciones"]
        solicitud = repositorio.obtener_solicitud(registro["id"], UUID(payload["clienteId"]))
        assert solicitud.cotizacion.coberturas[0].suma_asegurada.monto == Decimal(
            datos["insuredAmount"]
        )
        assert datos["location"] in oferta["descripcion"]
        assert datos["insuredAmount"] in " ".join(oferta["condiciones"])
        assert datos["startDate"] in oferta["vigencia"]
        assert datos["endDate"] in oferta["vigencia"]
        assert "umbral" in " ".join(oferta["condiciones"])
    if producto_id == "parametric" and datos.get("coverageType") in {
        "missed-connection",
        "comprehensive",
    }:
        assert "No cubre llegar tarde al aeropuerto" in " ".join(oferta["condiciones"])
        assert all(
            "Pérdida de conexión" in " ".join(item["coberturas"]) for item in ofertas["ofertas"]
        )
        if datos["coverageType"] == "missed-connection":
            assert all(
                "Cancelación" not in " ".join(item["coberturas"]) for item in ofertas["ofertas"]
            )
        else:
            assert all("Cancelación" in " ".join(item["coberturas"]) for item in ofertas["ofertas"])


@pytest.mark.parametrize("producto_id", DATOS_PRODUCTO)
@pytest.mark.parametrize("error", ["extra", "faltante", "cruzado"])
def test_rechaza_campos_extra_faltantes_o_de_otro_ramo(client, producto_id, error) -> None:
    datos = dict(DATOS_PRODUCTO[producto_id])
    if error == "extra":
        datos["campoAjeno"] = "no"
    elif error == "faltante":
        datos.pop(next(iter(datos)))
    else:
        otro_producto = "life" if producto_id != "life" else "automotive"
        datos = DATOS_PRODUCTO[otro_producto]
    respuesta = client.post(
        "/v1/solicitudes-cotizacion",
        json=nueva_solicitud(producto_id=producto_id, datos_producto=datos),
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert respuesta.status_code == 400


@pytest.mark.parametrize(
    ("producto_id", "cambios"),
    [
        ("parametric", {"coverageType": ""}),
        ("parametric", {"coverageType": "baggage"}),
        ("parametric", {"airline": "   "}),
        ("parametric", {"flightNumber": "AV-123"}),
        ("parametric", {"flightDate": "2026-02-30"}),
        ("parametric", {"flightDate": date.today().isoformat()}),
        ("parametric", {"flightDate": "2020-01-01"}),
        ("parametric-weather", {"location": " \t "}),
        ("parametric-weather", {"location": "x" * 121}),
        ("parametric-weather", {"weatherEvent": "Heavy-Rain"}),
        ("parametric-weather", {"startDate": "2026-02-30"}),
        ("parametric-weather", {"endDate": "2026-2-3"}),
        ("parametric-weather", {"startDate": date.today().isoformat()}),
        ("parametric-weather", {"endDate": date.today().isoformat()}),
        ("parametric-weather", {"endDate": (date.today() + timedelta(days=367)).isoformat()}),
        ("parametric-weather", {"insuredAmount": "0"}),
        ("parametric-weather", {"insuredAmount": "-1"}),
        ("parametric-weather", {"insuredAmount": "1.5"}),
        ("parametric-weather", {"insuredAmount": "01"}),
        ("parametric-weather", {"insuredAmount": "1e3"}),
        ("parametric-weather", {"insuredAmount": "1000000001"}),
        ("parametric-weather", {"insuredAmount": "9" * 5000}),
        ("parametric-weather", {"insuredAmount": 1000}),
        ("parametric-weather", {"airline": "No aplica"}),
    ],
)
def test_rechaza_variantes_invalidas_de_vuelo_y_clima(client, producto_id, cambios) -> None:
    respuesta = client.post(
        "/v1/solicitudes-cotizacion",
        json=nueva_solicitud(
            producto_id=producto_id, datos_producto={**DATOS_PRODUCTO[producto_id], **cambios}
        ),
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert respuesta.status_code == 400


@pytest.mark.parametrize("monto", ["1", "1000000000"])
def test_acepta_limites_de_monto_y_periodo_climatico(client, monto) -> None:
    respuesta = client.post(
        "/v1/solicitudes-cotizacion",
        json=nueva_solicitud(
            producto_id="parametric-weather",
            datos_producto={
                **DATOS_PRODUCTO["parametric-weather"],
                "location": "x" * 120,
                "insuredAmount": monto,
                "endDate": (date.today() + timedelta(days=366)).isoformat(),
            },
        ),
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert respuesta.status_code == 201


def test_rechaza_productos_desconocidos_sin_fallback_automotriz(client) -> None:
    respuesta = client.post(
        "/v1/solicitudes-cotizacion",
        json=nueva_solicitud(producto_id="desconocido", datos_producto={"vehiclePlate": "ABC123"}),
        headers={"Idempotency-Key": str(uuid4())},
    )
    assert respuesta.status_code == 400
