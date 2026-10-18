import json
from io import BytesIO
from unittest.mock import patch
from uuid import uuid4

import pytest
from bff_web.infrastructure.http_quote_gateway import HttpQuoteGateway
from flask import Flask
from support import bearer


@pytest.mark.parametrize("period", ["mensual", "por_viaje", "por_vuelo", "por_periodo", None])
def test_bff_preserva_prima_y_periodicidad_en_ofertas_y_seleccion(
    app: Flask, token: str, period: str | None
) -> None:
    money = {"monto": "99000.00", "moneda": "COP"}
    price: dict[str, dict[str, str] | str | None] = {
        "primaMensual": money if period in ("mensual", None) else None
    }
    if period is not None:
        price.update(prima=money, periodicidad=period)
    plan = {
        "id": str(uuid4()),
        "version": 1,
        "nombre": "Plan de prueba",
        "descripcion": "Daños al vehículo y responsabilidad civil",
        **price,
        "coberturaPrincipal": "Base asegurada",
        "deducible": "Sin descuento",
        "vigencia": "Por viaje" if period == "por_viaje" else "Periodo declarado",
        "responsabilidadCivil": "No aplica",
        "coberturas": ["Daños al vehículo", "Responsabilidad civil"],
        "condiciones": ["Simulación", "Deducible por evento"],
        "beneficiosAdicionales": [
            "Orientación sobre las condiciones del plan",
            "Orientación sobre daños al vehículo",
            "Orientación sobre responsabilidad civil",
        ],
        "recomendada": True,
        "estado": "disponible",
        "vigenteHasta": "2026-11-01T00:00:00Z",
    }
    offers_payload = {
        "solicitudId": "AQT-2026-ABCDEF1234",
        "calculadaEn": "2026-10-17T00:00:00Z",
        "vigenteHasta": "2026-11-01T00:00:00Z",
        "tasaAplicada": "Académica",
        "ofertas": [plan],
    }
    selection_payload = {
        "solicitudId": offers_payload["solicitudId"],
        "ofertaId": plan["id"],
        "version": 1,
        "nombreOferta": plan["nombre"],
        **price,
        "seleccionadaEn": "2026-10-17T00:00:00Z",
    }
    app.extensions["quote_gateway"] = HttpQuoteGateway("http://cotizacion.test")
    client = app.test_client()
    base = f"/v1/solicitudes-cotizacion/{offers_payload['solicitudId']}"
    with patch(
        "bff_web.infrastructure.http_quote_gateway.urlopen",
        side_effect=[
            BytesIO(json.dumps(payload).encode())
            for payload in (offers_payload, selection_payload, selection_payload)
        ],
    ):
        offers_response = client.get(f"{base}/ofertas", headers=bearer(token))
        selection_response = client.get(f"{base}/seleccion", headers=bearer(token))
        selected_response = client.put(
            f"{base}/seleccion",
            headers={**bearer(token), "Idempotency-Key": str(uuid4())},
            json={"ofertaId": plan["id"], "version": 1},
        )
    for response in (offers_response, selection_response, selected_response):
        assert response.status_code == 200
        result = response.get_json()
        if "ofertas" in result:
            result = result["ofertas"][0]
            for field in (
                "descripcion",
                "coberturaPrincipal",
                "deducible",
                "responsabilidadCivil",
                "coberturas",
                "condiciones",
                "beneficiosAdicionales",
            ):
                assert result[field] == plan[field]
        assert result["prima"] == money
        assert result["periodicidad"] == (period or "mensual")
        assert result["primaMensual"] == price["primaMensual"]
