import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from email.message import Message
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pymupdf
import pytest
import yaml
from alembic import command
from alembic.config import Config as AlembicConfig
from bff_web.domain.auth import User
from bff_web.domain.policy_renewal import RenewalServiceUnavailableError
from cryptography.hazmat.primitives.asymmetric import rsa
from openapi_schema_validator import OAS31Validator
from polizas.app import create_app
from polizas.domain.documento import archivo_html, canonico
from polizas.domain.documento_pdf import archivo_pdf
from polizas.infrastructure.models import (
    AceptacionDocumentoModel,
    AuditoriaPolizaModel,
    DocumentoPolizaModel,
    PolizaModel,
)
from pypdf import PdfReader
from pypdf.generic import DictionaryObject
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.orm import Session

from tests import test_policy_renewal_flow as renewal_flow
from tests.test_dashboard_flow import ROOT, USER, sign_token
from tests.test_policy_renewal_flow import (
    INTERNAL_TOKEN,
    Runtime,
    headers,
    issue_policy,
)

keypair = renewal_flow.keypair
runtime = renewal_flow.runtime
CONTRACT = yaml.safe_load(
    (ROOT / "contracts" / "openapi" / "documento-poliza-web-v1.yaml").read_text(encoding="utf-8")
)


def validate(schema: str, payload: object) -> None:
    OAS31Validator(
        {"$ref": f"#/components/schemas/{schema}", "components": CONTRACT["components"]},
        format_checker=OAS31Validator.FORMAT_CHECKER,
    ).validate(payload)


def confirmation(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "documentoId": document["documentoId"],
        "versionPoliza": document["versionPoliza"],
        "contenidoHash": document["contenidoHash"],
        "aceptaSimulacion": True,
    }


def count(runtime: Runtime, model: type[object]) -> int:
    engine = create_engine(runtime.database_url)
    try:
        with Session(engine) as session:
            return int(session.scalar(select(func.count()).select_from(model)) or 0)
    finally:
        engine.dispose()


def test_documento_html_autoritativo_estable_aceptacion_y_reinicio(
    runtime: Runtime,
    keypair: rsa.RSAPrivateKey,
) -> None:
    token = sign_token(keypair, scope="cliente")
    policy = issue_policy(runtime)
    path = f"/v1/clientes/me/polizas/{policy['id']}"
    state = runtime.bff.get(path + "/documento", headers=headers(token))
    assert state.status_code == 200
    document = state.get_json()
    validate("Documento", document)
    assert document["aceptacionActual"] is None and document["historialAceptaciones"] == []
    assert document["contenido"]["clienteId"] == str(USER.user_id)
    assert document["contenido"]["suscripcionId"] == policy["suscripcionId"]
    assert document["contenido"]["producto"]["tipo"] == "life"
    assert document["contenido"]["prima"]["monto"] == "25000"
    assert document["contenido"]["vigencia"]["fin"] == policy["fechaFin"]
    assert document["versionPoliza"] == document["contenido"]["version"] == 1
    assert document["contenido"]["coberturas"][0]["incluida"] is True
    assert document["contenido"]["condiciones"]
    assert "Sin validez legal" in document["aviso"]

    url = path + f"/documentos/{document['documentoId']}/descarga"
    download = runtime.bff.get(url, headers=headers(token))
    assert download.status_code == 200
    assert download.content_type == "text/html; charset=utf-8"
    assert download.headers["Content-Disposition"].endswith('-simulada.html"')
    assert download.headers["X-Content-Type-Options"] == "nosniff"
    assert download.headers["Cache-Control"] == "no-store"
    assert "sandbox" in download.headers["Content-Security-Policy"]
    assert hashlib.sha256(download.data).hexdigest() == document["contenidoHash"]
    assert download.headers["X-Content-SHA256"] == document["contenidoHash"]
    html = download.data.decode("utf-8")
    assert "SIMULADO" in html and "Sin validez legal" in html
    assert "Microseguro de Vida" in html and "25000 COP" in html
    assert "Fallecimiento" in html and policy["fechaFin"] in html
    assert "<script" not in html and "http://" not in html and "https://" not in html

    runtime.clock.on(date.fromisoformat(policy["fechaInicio"]) + timedelta(days=1))
    again = runtime.bff.get(path + "/documento", headers=headers(token)).get_json()
    assert again == document
    body, key = confirmation(document), str(uuid4())
    validate("Confirmacion", body)
    accepted = runtime.bff.post(path + "/aceptaciones", json=body, headers=headers(token, key))
    assert accepted.status_code == 201
    evidence = accepted.get_json()
    validate("Aceptacion", evidence)
    assert evidence["actorId"] == str(USER.user_id)
    assert datetime.fromisoformat(evidence["aceptadaEn"]) == runtime.clock.now
    replay = runtime.bff.post(path + "/aceptaciones", json=body, headers=headers(token, key))
    assert replay.status_code == 200 and replay.get_json() == evidence
    current = runtime.bff.get(path + "/documento", headers=headers(token)).get_json()
    validate("Documento", current)
    assert current["aceptacionActual"] == evidence
    assert current["historialAceptaciones"] == [evidence]
    assert runtime.bff.get(url, headers=headers(token)).data == download.data
    assert count(runtime, DocumentoPolizaModel) == count(runtime, AceptacionDocumentoModel) == 1
    assert count(runtime, AuditoriaPolizaModel) == 1

    restarted = create_app(runtime.database_url, INTERNAL_TOKEN)
    try:
        result = restarted.test_client().get(
            url,
            headers={
                "Authorization": f"Bearer {INTERNAL_TOKEN}",
                "X-Cliente-ID": str(USER.user_id),
            },
        )
        assert result.status_code == 200 and result.data == download.data
    finally:
        restarted.extensions["polizas_engine"].dispose()


def test_ownership_jwt_no_admite_suplantar_actor(
    runtime: Runtime,
    keypair: rsa.RSAPrivateKey,
) -> None:
    token = sign_token(keypair, scope="cliente")
    other_user = User(uuid4(), "Otra", "otra@example.test", "cliente")
    other = sign_token(keypair, other_user, scope="cliente")
    advisor = sign_token(
        keypair,
        User(uuid4(), "Socia", "socia@example.test", "socio"),
        scope="socio",
    )
    policy = issue_policy(runtime)
    path = f"/v1/clientes/me/polizas/{policy['id']}"
    document = runtime.bff.get(path + "/documento", headers=headers(token)).get_json()
    download = path + f"/documentos/{document['documentoId']}/descarga"
    body = confirmation(document)
    for url in (path + "/documento", download, download + "/pdf"):
        assert runtime.bff.get(url).status_code == 401
        assert runtime.bff.get(url, headers=headers(advisor)).status_code == 403
        response = runtime.bff.get(
            url, headers={**headers(other), "X-Cliente-ID": str(USER.user_id)}
        )
        assert response.status_code == 404
        validate("Problema", response.get_json())
        assert response.headers["Cache-Control"] == "no-store"
    assert runtime.bff.post(path + "/aceptaciones", json=body).status_code == 401
    assert (
        runtime.bff.post(
            path + "/aceptaciones", json=body, headers=headers(advisor, str(uuid4()))
        ).status_code
        == 403
    )
    assert (
        runtime.bff.post(
            path + "/aceptaciones",
            json=body,
            headers={**headers(other, str(uuid4())), "X-Cliente-ID": str(USER.user_id)},
        ).status_code
        == 404
    )
    for extra in ({"actorId": str(other_user.user_id)}, {"aceptadaEn": "2020-01-01T00:00:00Z"}):
        assert (
            runtime.bff.post(
                path + "/aceptaciones", json={**body, **extra}, headers=headers(token, str(uuid4()))
            ).status_code
            == 400
        )
    assert (
        runtime.bff.get(
            f"/v1/clientes/me/polizas/{uuid4()}/documentos/{document['documentoId']}/descarga",
            headers=headers(token),
        ).status_code
        == 404
    )
    owned_other = issue_policy(runtime)
    assert (
        runtime.bff.get(
            f"/v1/clientes/me/polizas/{owned_other['id']}/documentos/{document['documentoId']}/descarga",
            headers=headers(token),
        ).status_code
        == 404
    )
    assert count(runtime, AceptacionDocumentoModel) == 0


def test_pdf_desde_snapshot_persistido_determinista_y_render_legible(
    runtime: Runtime,
    keypair: rsa.RSAPrivateKey,
    tmp_path: Path,
) -> None:
    token = sign_token(keypair, scope="cliente")
    policy = issue_policy(runtime)
    path = f"/v1/clientes/me/polizas/{policy['id']}"
    document = runtime.bff.get(path + "/documento", headers=headers(token)).get_json()
    download_path = path + f"/documentos/{document['documentoId']}/descarga"
    html = runtime.bff.get(download_path, headers=headers(token)).data
    before = [
        count(runtime, model)
        for model in (
            DocumentoPolizaModel,
            AceptacionDocumentoModel,
            AuditoriaPolizaModel,
        )
    ]
    response = runtime.bff.get(download_path + "/pdf", headers=headers(token))
    assert response.status_code == 200
    assert response.content_type == "application/pdf"
    assert response.headers["Content-Disposition"] == (
        f'attachment; filename="poliza-{policy["id"]}-{document["documentoId"]}-simulada.pdf"'
    )
    assert response.headers["X-Content-SHA256"] == hashlib.sha256(response.data).hexdigest()
    assert response.headers["X-Document-Content-SHA256"] == document["contenidoHash"]
    assert response.headers["X-Content-SHA256"] != document["contenidoHash"]
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in response.headers["Content-Security-Policy"]
    assert runtime.bff.get(download_path + "/pdf", headers=headers(token)).data == response.data
    assert runtime.bff.get(download_path, headers=headers(token)).data == html
    assert runtime.bff.get(path + "/documento", headers=headers(token)).get_json() == document
    assert before == [
        count(runtime, model)
        for model in (
            DocumentoPolizaModel,
            AceptacionDocumentoModel,
            AuditoriaPolizaModel,
        )
    ]
    reader = PdfReader(io.BytesIO(response.data), strict=True)
    text = "\n".join(page.extract_text() for page in reader.pages)
    for value in (
        "Solventa",
        "Seguros y tranquilidad",
        "SIMULADO",
        "Sin validez legal",
        "Microseguro de Vida",
        "25000 COP",
        "Fallecimiento",
        policy["fechaFin"],
        policy["id"],
        document["documentoId"],
        str(USER.user_id),
    ):
        assert value in text
    assert reader.metadata is not None
    subject = reader.metadata.subject
    assert isinstance(subject, str)
    assert document["contenidoHash"] in subject
    assert not reader.is_encrypted
    root = reader.trailer["/Root"]
    assert isinstance(root, DictionaryObject)
    assert "/AcroForm" not in root
    assert "/OpenAction" not in root
    (tmp_path / "documento.pdf").write_bytes(response.data)
    (tmp_path / "documento.json").write_text(
        json.dumps(
            {
                "documento": document,
                "headers": {
                    name: response.headers[name]
                    for name in (
                        "Content-Type",
                        "Content-Disposition",
                        "X-Content-SHA256",
                        "X-Document-Content-SHA256",
                    )
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    with pymupdf.open(stream=response.data, filetype="pdf") as rendered:
        page = rendered[0]
        pixmap = page.get_pixmap(alpha=False)
        pixmap.save(tmp_path / "documento.png")
        assert pixmap.width >= 595 and pixmap.height >= 842
        samples = pixmap.samples
        primary_pixels = sum(
            tuple(samples[index : index + 3]) == (0, 124, 165)
            for index in range(0, len(samples), pixmap.n)
        )
        assert primary_pixels > 100
    restarted = create_app(runtime.database_url, INTERNAL_TOKEN)
    try:
        reloaded = restarted.test_client().get(
            download_path + "/pdf",
            headers={
                "Authorization": f"Bearer {INTERNAL_TOKEN}",
                "X-Cliente-ID": str(USER.user_id),
            },
        )
        assert reloaded.status_code == 200 and reloaded.data == response.data
    finally:
        restarted.extensions["polizas_engine"].dispose()


@pytest.mark.parametrize(
    "failure",
    ["digest", "mime", "canonical", "association", "structure", "limite", "exceso", "vacio"],
)
def test_pdf_transporte_verifica_integridad_y_limite(
    runtime: Runtime,
    keypair: rsa.RSAPrivateKey,
    failure: str,
) -> None:
    token = sign_token(keypair, scope="cliente")
    policy = issue_policy(runtime)
    path = f"/v1/clientes/me/polizas/{policy['id']}"
    document = runtime.bff.get(path + "/documento", headers=headers(token)).get_json()
    url = path + f"/documentos/{document['documentoId']}/descarga/pdf"
    valid = runtime.bff.get(url, headers=headers(token))
    assert valid.status_code == 200
    payload = b"%PDF-1.4\ninvalid document\n%%EOF" if failure == "structure" else valid.data
    if failure in ("limite", "exceso"):
        payload = valid.data.ljust(1_048_576 + (failure == "exceso"), b" ")
    elif failure == "vacio":
        payload = b""

    class Reply(io.BytesIO):
        status = 200

        def __init__(self) -> None:
            super().__init__(payload)
            self.headers = Message()
            self.headers["Content-Type"] = "text/html" if failure == "mime" else "application/pdf"
            self.headers["X-Content-SHA256"] = (
                document["contenidoHash"]
                if failure == "digest"
                else hashlib.sha256(payload).hexdigest()
            )
            self.headers["X-Document-Content-SHA256"] = (
                "a" * 64 if failure == "canonical" else document["contenidoHash"]
            )
            self.headers["Content-Disposition"] = (
                'attachment; filename="otro-documento.pdf"'
                if failure == "association"
                else valid.headers["Content-Disposition"]
            )

    with patch("bff_web.infrastructure.http_policy_document_gateway.urlopen", return_value=Reply()):
        response = runtime.bff.get(url, headers=headers(token))
    if failure == "limite":
        assert response.status_code == 200 and response.data == payload
        return
    assert response.status_code == 503
    assert response.get_json()["code"] == "servicio_no_disponible"
    assert response.headers["Retry-After"] == "5"
    assert response.content_type == "application/problem+json"


def test_pdf_no_materializa_y_rechaza_snapshot_incoherente(
    runtime: Runtime,
    keypair: rsa.RSAPrivateKey,
) -> None:
    token = sign_token(keypair, scope="cliente")
    policy = issue_policy(runtime)
    path = f"/v1/clientes/me/polizas/{policy['id']}"
    missing = path + f"/documentos/{uuid4()}/descarga/pdf"
    assert runtime.bff.get(missing, headers=headers(token)).status_code == 404
    assert count(runtime, DocumentoPolizaModel) == 0
    document = runtime.bff.get(path + "/documento", headers=headers(token)).get_json()
    url = path + f"/documentos/{document['documentoId']}/descarga/pdf"
    assert (
        runtime.bff.get(url + "?clienteId=" + str(USER.user_id), headers=headers(token)).status_code
        == 400
    )
    assert runtime.bff.get(url, data=b"{}", headers=headers(token)).status_code == 400
    owned_other = issue_policy(runtime)
    assert (
        runtime.bff.get(
            f"/v1/clientes/me/polizas/{owned_other['id']}"
            f"/documentos/{document['documentoId']}/descarga/pdf",
            headers=headers(token),
        ).status_code
        == 404
    )
    engine = create_engine(runtime.database_url)
    try:
        with Session(engine) as session, session.begin():
            stored = session.scalar(select(DocumentoPolizaModel))
            assert stored is not None
            stored.contenido = stored.contenido.replace("25000", "90000")
        response = runtime.bff.get(url, headers=headers(token))
        assert response.status_code == 503
        assert response.get_json()["code"] == "servicio_no_disponible"
        assert response.headers["Retry-After"] == "5"
        assert count(runtime, AceptacionDocumentoModel) == count(runtime, AuditoriaPolizaModel) == 0
    finally:
        engine.dispose()


def test_pdf_pagina_condiciones_largas_y_escapa_marcas_sin_recursos_externos(
    runtime: Runtime,
    keypair: rsa.RSAPrivateKey,
) -> None:
    token = sign_token(keypair, scope="cliente")
    policy = issue_policy(runtime)
    document = runtime.bff.get(
        f"/v1/clientes/me/polizas/{policy['id']}/documento", headers=headers(token)
    ).get_json()
    content = document["contenido"]
    content["condiciones"] = [
        "\u00c1mbito e informaci\u00f3n verificable. " + "Condicion del modelo academico. " * 20
        for _ in range(30)
    ]
    content["condiciones"].append('<img src="https://externo.test/logo.png">')
    canonical = canonico(content)
    html_hash = hashlib.sha256(archivo_html(canonical).encode("utf-8")).hexdigest()
    with patch(
        "urllib.request.urlopen", side_effect=AssertionError("Recurso externo no permitido")
    ):
        pdf = archivo_pdf(canonical, UUID(document["documentoId"]), html_hash)
    reader = PdfReader(io.BytesIO(pdf), strict=True)
    assert len(reader.pages) > 1
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text()
        assert "SIMULADO" in text and f"Pagina {index}" in text
        assert not page.get("/Annots")
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "\u00c1mbito e informaci\u00f3n verificable" in text
    assert '<img src="https://externo.test/logo.png">' in text


def test_rechazos_hash_version_clave_y_aceptacion_explicita(
    runtime: Runtime,
    keypair: rsa.RSAPrivateKey,
) -> None:
    token = sign_token(keypair, scope="cliente")
    policy = issue_policy(runtime)
    path = f"/v1/clientes/me/polizas/{policy['id']}"
    document = runtime.bff.get(path + "/documento", headers=headers(token)).get_json()
    body, key = confirmation(document), str(uuid4())
    for delta in (
        {"aceptaSimulacion": False},
        {"aceptaSimulacion": "true"},
        {"versionPoliza": True},
        {"contenidoHash": "incorrecto"},
    ):
        assert (
            runtime.bff.post(
                path + "/aceptaciones", json={**body, **delta}, headers=headers(token, key)
            ).status_code
            == 400
        )
    assert (
        runtime.bff.post(path + "/aceptaciones", json=body, headers=headers(token)).status_code
        == 400
    )
    for stale_delta in ({"versionPoliza": 2}, {"contenidoHash": "a" * 64}):
        response = runtime.bff.post(
            path + "/aceptaciones", json={**body, **stale_delta}, headers=headers(token, key)
        )
        assert response.status_code == 409
        assert response.get_json()["code"] == "documento_desactualizado"
    assert (
        runtime.bff.post(path + "/aceptaciones", json=body, headers=headers(token, key)).status_code
        == 201
    )
    response = runtime.bff.post(
        path + "/aceptaciones",
        json={**body, "contenidoHash": "a" * 64},
        headers=headers(token, key),
    )
    assert response.status_code == 409
    assert response.get_json()["code"] == "idempotencia_conflictiva"
    validate("Problema", response.get_json())


@pytest.mark.parametrize("change", ["modificacion", "renovacion", "cancelacion"])
def test_lifecycle_exige_nuevo_documento_y_preserva_evidencia(
    runtime: Runtime,
    keypair: rsa.RSAPrivateKey,
    change: str,
) -> None:
    token = sign_token(keypair, scope="cliente")
    policy = issue_policy(runtime)
    path = f"/v1/clientes/me/polizas/{policy['id']}"
    document = runtime.bff.get(path + "/documento", headers=headers(token)).get_json()
    body, key = confirmation(document), str(uuid4())
    original = runtime.bff.post(
        path + "/aceptaciones", json=body, headers=headers(token, key)
    ).get_json()
    download_path = path + f"/documentos/{document['documentoId']}/descarga"
    original_bytes = runtime.bff.get(download_path, headers=headers(token)).data
    original_pdf = runtime.bff.get(download_path + "/pdf", headers=headers(token))
    assert original_pdf.status_code == 200
    runtime.clock.on(date.fromisoformat(policy["fechaInicio"]) + timedelta(days=2))
    if change == "modificacion":
        changes = {"versionBase": 1, "plan": "premium", "coberturasOpcionales": []}
        simulation = runtime.bff.post(
            path + "/modificaciones/simulacion", json=changes, headers=headers(token)
        ).get_json()
        changed = runtime.bff.post(
            path + "/modificaciones",
            json={**changes, "confirma": True, "impactoHash": simulation["impactoHash"]},
            headers=headers(token, str(uuid4())),
        )
    elif change == "renovacion":
        runtime.clock.on(date.fromisoformat(policy["fechaFin"]) - timedelta(days=1))
        offer = runtime.bff.get(path + "/renovacion", headers=headers(token)).get_json()["oferta"]
        changed = runtime.bff.post(
            path + "/renovaciones",
            json={"aceptaCondiciones": True, "condicionesHash": offer["condicionesHash"]},
            headers=headers(token, str(uuid4())),
        )
    else:
        settlement = runtime.bff.get(path + "/cancelacion", headers=headers(token)).get_json()[
            "liquidacion"
        ]
        changed = runtime.bff.post(
            path + "/cancelaciones",
            json={
                "confirma": True,
                "motivoCancelacion": "costo",
                "liquidacionHash": settlement["liquidacionHash"],
            },
            headers=headers(token, str(uuid4())),
        )
    assert changed.status_code == 201, changed.get_json()
    current = runtime.bff.get(path + "/documento", headers=headers(token)).get_json()
    validate("Documento", current)
    assert current["contenidoHash"] != document["contenidoHash"]
    assert current["documentoId"] != document["documentoId"]
    assert current["versionPoliza"] == (2 if change == "modificacion" else 1)
    assert current["aceptacionActual"] is None
    assert current["historialAceptaciones"] == [original]
    assert runtime.bff.get(download_path, headers=headers(token)).data == original_bytes
    historical_pdf = runtime.bff.get(download_path + "/pdf", headers=headers(token))
    assert historical_pdf.status_code == 200 and historical_pdf.data == original_pdf.data
    replay = runtime.bff.post(path + "/aceptaciones", json=body, headers=headers(token, key))
    assert replay.status_code == 200 and replay.get_json() == original
    stale = runtime.bff.post(
        path + "/aceptaciones", json=body, headers=headers(token, str(uuid4()))
    )
    assert stale.status_code == 409 and stale.get_json()["code"] == "documento_desactualizado"
    fresh = runtime.bff.post(
        path + "/aceptaciones", json=confirmation(current), headers=headers(token, str(uuid4()))
    )
    assert fresh.status_code == 201
    assert count(runtime, AceptacionDocumentoModel) == 2
    if change == "cancelacion":
        assert current["contenido"]["estado"] == "cancelada"
        engine = create_engine(runtime.database_url)
        with Session(engine) as session:
            assert session.scalar(select(PolizaModel.estado)) == "cancelada"
        engine.dispose()


def test_producto_por_fechas_no_inventa_prima(
    runtime: Runtime,
    keypair: rsa.RSAPrivateKey,
) -> None:
    token = sign_token(keypair, scope="cliente")
    response = runtime.policies.post(
        "/v1/polizas",
        json={
            "clienteId": str(USER.user_id),
            "suscripcionId": str(uuid4()),
            "pagoId": str(uuid4()),
            "tipo": "travel",
            "confirmadaEn": runtime.clock.now.isoformat(),
            "simulada": True,
            "fechasContratadas": {
                "inicio": runtime.clock.now.date().isoformat(),
                "fin": (runtime.clock.now.date() + timedelta(days=5)).isoformat(),
            },
        },
        headers={"Authorization": f"Bearer {INTERNAL_TOKEN}"},
    )
    assert response.status_code == 200
    path = f"/v1/clientes/me/polizas/{response.get_json()['id']}"
    document = runtime.bff.get(path + "/documento", headers=headers(token)).get_json()
    validate("Documento", document)
    assert document["versionPoliza"] is None and document["contenido"]["prima"] is None
    assert document["contenido"]["coberturas"][0]["codigo"] == "asistencia_medica"
    pdf = runtime.bff.get(
        path + f"/documentos/{document['documentoId']}/descarga/pdf", headers=headers(token)
    )
    assert pdf.status_code == 200
    assert "No disponible en este modelo" in "\n".join(
        page.extract_text() for page in PdfReader(io.BytesIO(pdf.data)).pages
    )
    assert (
        runtime.bff.post(
            path + "/aceptaciones",
            json=confirmation(document),
            headers=headers(token, str(uuid4())),
        ).status_code
        == 201
    )


def test_html_escapa_campos_del_modelo(runtime: Runtime, keypair: rsa.RSAPrivateKey) -> None:
    token = sign_token(keypair, scope="cliente")
    policy = issue_policy(runtime)
    document = runtime.bff.get(
        f"/v1/clientes/me/polizas/{policy['id']}/documento", headers=headers(token)
    ).get_json()
    content = document["contenido"]
    content["producto"]["nombre"] = '<script src="https://malicioso.test/"></script>'
    content["condiciones"] = ['<img src=x onerror="alert(1)">']
    html = archivo_html(canonico(content))
    assert "<script" not in html and "<img" not in html
    assert "&lt;script" in html and "&lt;img" in html


def test_503_no_finge_documento_ni_aceptacion(
    runtime: Runtime,
    keypair: rsa.RSAPrivateKey,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = sign_token(keypair, scope="cliente")
    policy = issue_policy(runtime)
    path = f"/v1/clientes/me/polizas/{policy['id']}"
    document = runtime.bff.get(path + "/documento", headers=headers(token)).get_json()
    gateway = runtime.bff.application.extensions["policy_document_gateway"]

    def unavailable(*args: object, **kwargs: object) -> None:
        raise RenewalServiceUnavailableError()

    monkeypatch.setattr(gateway, "_call", unavailable)
    for response in (
        runtime.bff.get(path + "/documento", headers=headers(token)),
        runtime.bff.post(
            path + "/aceptaciones",
            json=confirmation(document),
            headers=headers(token, str(uuid4())),
        ),
    ):
        assert response.status_code == 503
        assert response.get_json()["code"] == "servicio_no_disponible"
        assert response.headers["Retry-After"] == "5"
        assert response.headers["Cache-Control"] == "no-store"
        validate("Problema", response.get_json())
    assert count(runtime, AceptacionDocumentoModel) == 0


def test_dto_incompleto_de_polizas_no_es_documento_valido(
    runtime: Runtime,
    keypair: rsa.RSAPrivateKey,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = sign_token(keypair, scope="cliente")
    policy = issue_policy(runtime)
    path = f"/v1/clientes/me/polizas/{policy['id']}/documento"
    malformed = runtime.bff.get(path, headers=headers(token)).get_json()
    del malformed["contenido"]["coberturas"]
    gateway = runtime.bff.application.extensions["policy_document_gateway"]

    def corrupt(*args: object, **kwargs: object) -> tuple[object, int]:
        return malformed, 200

    monkeypatch.setattr(gateway, "_call", corrupt)
    response = runtime.bff.get(path, headers=headers(token))
    assert response.status_code == 503
    assert response.get_json()["code"] == "servicio_no_disponible"


@pytest.mark.parametrize(
    "mime,checksum",
    [
        ("text/html; charset=utf-8", "a" * 64),
        ("application/json; charset=utf-8", hashlib.sha256(b"<html>alterado</html>").hexdigest()),
    ],
)
def test_descarga_corrupta_o_mime_incorrecto_no_se_publica(
    runtime: Runtime,
    keypair: rsa.RSAPrivateKey,
    mime: str,
    checksum: str,
) -> None:
    token = sign_token(keypair, scope="cliente")
    policy = issue_policy(runtime)
    path = f"/v1/clientes/me/polizas/{policy['id']}"
    document = runtime.bff.get(path + "/documento", headers=headers(token)).get_json()

    class Reply(io.BytesIO):
        status = 200

        def __init__(self) -> None:
            super().__init__(b"<html>alterado</html>")
            self.headers = Message()
            self.headers["Content-Type"] = mime
            self.headers["X-Content-SHA256"] = checksum

    with patch("bff_web.infrastructure.http_policy_document_gateway.urlopen", return_value=Reply()):
        response = runtime.bff.get(
            path + f"/documentos/{document['documentoId']}/descarga", headers=headers(token)
        )
    assert response.status_code == 503
    assert response.get_json()["code"] == "servicio_no_disponible"
    assert b"<html>alterado</html>" not in response.data


def test_migracion_ida_vuelta_conserva_polizas(runtime: Runtime) -> None:
    issue_policy(runtime)
    config = AlembicConfig(str(ROOT / "services" / "polizas" / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", runtime.database_url)
    command.downgrade(config, "polizas_0004")
    engine = create_engine(runtime.database_url)
    try:
        assert "documentos_poliza" not in inspect(engine).get_table_names()
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(PolizaModel)) == 1
        command.upgrade(config, "head")
        assert {"documentos_poliza", "aceptaciones_documento"} <= set(
            inspect(engine).get_table_names()
        )
    finally:
        engine.dispose()


def test_consulta_y_aceptacion_concurrentes_no_duplican(runtime: Runtime) -> None:
    policy = issue_policy(runtime)
    path = f"/v1/clientes/me/polizas/{policy['id']}"
    internal_headers = {
        "Authorization": f"Bearer {INTERNAL_TOKEN}",
        "X-Cliente-ID": str(USER.user_id),
    }
    app = runtime.policies.application

    def consult(_: int) -> dict[str, Any]:
        with app.test_client() as client:
            response = client.get(path + "/documento", headers=internal_headers)
            assert response.status_code == 200, response.get_json()
            return dict(response.get_json())

    with ThreadPoolExecutor(max_workers=4) as pool:
        documents = list(pool.map(consult, range(4)))
    assert len({d["documentoId"] for d in documents}) == 1
    key = str(uuid4())

    def accept(_: int) -> str:
        with app.test_client() as client:
            response = client.post(
                path + "/aceptaciones",
                json=confirmation(documents[0]),
                headers={**internal_headers, "Idempotency-Key": key},
            )
            assert response.status_code in {200, 201}, response.get_json()
            return str(response.get_json()["id"])

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert len(set(pool.map(accept, range(4)))) == 1
    assert count(runtime, AceptacionDocumentoModel) == 1
    assert count(runtime, AuditoriaPolizaModel) == 1
