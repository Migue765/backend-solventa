import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any
from urllib.request import Request
from uuid import uuid4

from cotizacion.app import create_app
from cotizacion.config import Config

INTERNAL_TOKEN = "internal-test-token"


class ProfilingServiceStub:
    def __init__(self) -> None:
        self.profiles: dict[str, dict[str, Any]] = {}
        self.created = 0

    def __call__(self, request: Request, timeout: float) -> BytesIO:
        assert timeout == 0.7
        if request.data is not None:
            body = json.loads(request.data)
            profile_id = str(uuid4())
            now = datetime.now(UTC)
            profile = {
                "perfilId": profile_id,
                "clienteId": body["clienteId"],
                "referenciaId": body["referenciaId"],
                "operationId": body["operationId"],
                "creditoId": body["creditoId"],
                "version": 1,
                "modeloVersion": "perfil-riesgo-1",
                "score": "0.35",
                "nivel": "A",
                "consentimientoId": body["consentimientoId"],
                "consentimientoVersion": body["consentimientoVersion"],
                "calculadoEn": (now - timedelta(seconds=1)).isoformat(),
                "vigenteHasta": (now + timedelta(minutes=15)).isoformat(),
                "fuentes": [
                    {
                        "nombre": name,
                        "version": "mock-1",
                        "consultadaEn": (now - timedelta(seconds=1)).isoformat(),
                        "vigenteHasta": (now + timedelta(minutes=15)).isoformat(),
                        "simulada": True,
                        "indicadorRiesgo": "0.35",
                    }
                    for name in ("open_finance", "open_data")
                ],
                "variables": [
                    {
                        "nombre": "indicador_riesgo",
                        "fuente": name,
                        "valorNormalizado": "0.35",
                        "autorizadaPor": body["consentimientoId"],
                    }
                    for name in ("open_finance", "open_data")
                ],
                "variablesExcluidas": [],
                "regla": {
                    "reglaId": "riesgo-maximo-fuentes",
                    "version": "1.0",
                    "modeloVersion": "perfil-riesgo-1",
                    "expresion": "max(indicador_riesgo_por_fuente)",
                },
                "degradaciones": [],
                "hashDecision": "a" * 64,
                "fallbackUsed": False,
            }
            self.profiles[profile_id] = profile
            self.created += 1
            return BytesIO(json.dumps(profile).encode())
        profile_id = request.full_url.split("/v1/perfiles/", 1)[1].split("?", 1)[0]
        return BytesIO(json.dumps(self.profiles[profile_id]).encode())


def _payload(operation_id: str, amount: int = 310_000_000) -> dict[str, object]:
    return {
        "operationId": operation_id,
        "socioId": "BANCO-001",
        "asesorId": str(uuid4()),
        "cliente": {
            "clienteId": str(uuid4()),
            "tipoDocumento": "cc",
            "numeroDocumento": "1023456789",
            "correo": "cliente@solventa.co",
            "telefono": "+573001234567",
            "fechaNacimiento": "1985-02-10",
        },
        "credito": {
            "referencia": "HIP-2026-001",
            "monto": amount,
            "plazoMeses": 240,
        },
        "consentimientoId": str(uuid4()),
        "consentimientoVersion": 2,
    }


def test_operation_is_completed_and_idempotent(
    database_url: str,
    monkeypatch: Any,
) -> None:
    profiling = ProfilingServiceStub()
    monkeypatch.setattr("cotizacion.infrastructure.perfil_http.urlopen", profiling)
    app = create_app(
        Config(
            database_url=database_url,
            internal_api_token=INTERNAL_TOKEN,
            perfilamiento_habilitado=True,
            perfilamiento_url="http://perfilamiento.test",
        )
    )
    client = app.test_client()
    operation_id = str(uuid4())
    payload = _payload(operation_id)
    headers = {
        "Authorization": f"Bearer {INTERNAL_TOKEN}",
        "Idempotency-Key": operation_id,
    }

    first = client.post("/v1/perfilamientos-hipotecarios", json=payload, headers=headers)
    retry = client.post("/v1/perfilamientos-hipotecarios", json=payload, headers=headers)

    assert first.status_code == retry.status_code == 201
    assert retry.get_json() == first.get_json()
    assert first.get_json()["estado"] == "completada"
    assert first.get_json()["operationId"] == operation_id
    assert first.get_json()["perfilamientoReferencia"]
    assert first.get_json()["solicitudId"].startswith("AQT-")
    assert profiling.created == 1
    profile = next(iter(profiling.profiles.values()))
    assert profile["operationId"] == operation_id
    assert profile["creditoId"] == "HIP-2026-001"


def test_same_operation_with_different_body_conflicts(
    database_url: str,
    monkeypatch: Any,
) -> None:
    profiling = ProfilingServiceStub()
    monkeypatch.setattr("cotizacion.infrastructure.perfil_http.urlopen", profiling)
    app = create_app(
        Config(
            database_url=database_url,
            internal_api_token=INTERNAL_TOKEN,
            perfilamiento_habilitado=True,
            perfilamiento_url="http://perfilamiento.test",
        )
    )
    client = app.test_client()
    operation_id = str(uuid4())
    payload = _payload(operation_id)
    headers = {
        "Authorization": f"Bearer {INTERNAL_TOKEN}",
        "Idempotency-Key": operation_id,
    }
    assert (
        client.post("/v1/perfilamientos-hipotecarios", json=payload, headers=headers).status_code
        == 201
    )

    payload["credito"] = {
        "referencia": "HIP-2026-001",
        "monto": 320_000_000,
        "plazoMeses": 240,
    }
    conflict = client.post("/v1/perfilamientos-hipotecarios", json=payload, headers=headers)

    assert conflict.status_code == 409
    assert conflict.get_json()["error"] == "conflicto_idempotencia"
    assert profiling.created == 1
