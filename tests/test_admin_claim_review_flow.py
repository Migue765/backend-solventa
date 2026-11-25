"""Identidad y BFF Web -> HTTP -> revision humana y persistencia de Siniestros."""

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig
from bff_web.app import create_app as create_bff
from bff_web.config import Config as BffConfig
from bff_web.infrastructure.jwt_verifier import JwksKeyStore, JwtVerifier
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask
from flask.testing import FlaskClient
from identidad.app import create_app as create_identity
from identidad.config import Config as IdentityConfig
from identidad.infrastructure.persistence.models import Base as IdentityBase
from siniestros.app import create_app as create_claims
from siniestros.domain import Config as ClaimsConfig
from siniestros.infrastructure.decisiones import DecisionSiniestroModel
from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.models import Audit, Claim, Upload
from siniestros.repository import fingerprint
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from tests.test_claims_mobile_status_flow import serve
from tests.test_dashboard_flow import INTERNAL_TOKEN, ISSUER, ROOT

PASSWORD = "OperadorSintetico123!"
Runtime = tuple[FlaskClient, Flask, Flask, dict[str, str], UUID]


@pytest.fixture
def runtime(tmp_path: Path) -> Iterator[Runtime]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    identity_url = f"sqlite:///{tmp_path / 'identidad.db'}"
    identity_engine = create_engine(identity_url)
    IdentityBase.metadata.create_all(identity_engine)
    identity_engine.dispose()
    identity = create_identity(
        IdentityConfig(
            database_url=identity_url,
            jwt_issuer=ISSUER,
            jwt_private_key=key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ).decode(),
            password_iterations=1000,
            internal_api_token=INTERNAL_TOKEN,
        )
    )
    created = identity.test_cli_runner().invoke(
        args=["create-admin", "--name", "Operador interno", "--email", "operador@example.net"],
        input=f"{PASSWORD}\n{PASSWORD}\n",
    )
    assert created.exit_code == 0, created.output
    admin_id = UUID(created.output.strip().splitlines()[-1])
    claims_url = f"sqlite:///{tmp_path / 'siniestros.db'}"
    migrations = AlembicConfig(str(ROOT / "services" / "siniestros" / "alembic.ini"))
    migrations.set_main_option("sqlalchemy.url", claims_url)
    command.upgrade(migrations, "head")
    claims = create_claims(
        ClaimsConfig(claims_url, tmp_path / "evidencias", INTERNAL_TOKEN, "http://polizas.test")
    )
    try:
        with serve(identity) as identity_http, serve(claims) as claims_http:
            bff = create_bff(
                BffConfig(
                    identity_url=identity_http,
                    jwt_issuer=ISSUER,
                    claims_service_url=claims_http,
                    internal_api_token=INTERNAL_TOKEN,
                ),
                token_verifier=JwtVerifier(
                    JwksKeyStore(identity.extensions["jwt_issuer"].jwks),
                    ISSUER,
                    "solventa-api",
                    "web",
                ),
            )
            client = bff.test_client()
            login = client.post(
                "/v1/sesiones",
                json={"correo": "operador@example.net", "contrasena": PASSWORD},
            )
            assert login.status_code == 200
            tokens = login.get_json()
            assert tokens["usuario"]["rol"] == "administrador"
            assert "socios" not in tokens["usuario"]
            yield client, claims, identity, tokens, admin_id
    finally:
        claims.extensions["siniestros_engine"].dispose()


def pending_claim(app: Flask) -> tuple[UUID, UUID]:
    claim_id, owner = uuid4(), uuid4()
    reported = datetime.now(UTC) - timedelta(hours=1)
    payload = {
        "polizaId": str(uuid4()),
        "fechaEvento": (reported - timedelta(hours=1)).isoformat(),
        "tipoEvento": "Accidente",
        "descripcion": "Aviso sintetico para revision humana.",
        "lugar": "Lugar de prueba",
    }
    with Session(app.extensions["siniestros_engine"]) as session, session.begin():
        session.add(
            Claim(
                id=str(claim_id),
                owner=str(owner),
                operation=str(uuid4()),
                fingerprint=fingerprint(payload),
                payload=payload,
                created=reported.isoformat(),
                tipo="asistido",
                estado="reportado",
                actualizado_en=reported,
            )
        )
        session.flush()
        session.add(
            CambioEstadoModel(
                siniestro_id=str(claim_id),
                secuencia=1,
                estado="reportado",
                ocurrido_en=reported,
                visible_cliente=True,
            )
        )
    result = app.test_client().post(
        f"/internal/v1/siniestros/{claim_id}/decisiones/automatica",
        headers={"Authorization": f"Bearer {INTERNAL_TOKEN}"},
        json={
            "evaluacionId": str(uuid4()),
            "evaluadoEn": datetime.now(UTC).isoformat(),
            "elegible": False,
            "requiereInspeccion": False,
            "fraude": {"nivel": "bajo", "bloqueante": False},
            "reglas": [{"reglaId": "revision-asistida", "version": 1, "resultado": "revision"}],
            "motivo": "El caso requiere revision humana.",
        },
    )
    assert result.status_code == 201
    return claim_id, owner


def test_revision_integrada_persiste_actor_estado_y_reintento(runtime: Runtime) -> None:
    client, claims, _, tokens, admin_id = runtime
    claim_id, owner = pending_claim(claims)
    headers = {"Authorization": f"Bearer {tokens['accessToken']}"}
    path = f"/v1/administracion/siniestros/{claim_id}"
    queue = client.get("/v1/administracion/colas-revision/revision/asignaciones", headers=headers)
    assert queue.status_code == 200
    assert queue.get_json()["asignaciones"][0]["siniestroId"] == str(claim_id)
    detail = client.get(path, headers=headers)
    assert detail.status_code == 200
    assert detail.get_json()["siniestro"]["id"] == str(claim_id)
    assert detail.get_json()["evidencias"] == []
    review_headers = {**headers, "Idempotency-Key": str(uuid4())}
    data = {"resultado": "aprobado", "motivo": "Revision humana completada sin hallazgos."}
    forged = client.post(
        path + "/decisiones/revision",
        headers=review_headers,
        json={**data, "revisorId": str(uuid4())},
    )
    assert forged.status_code == 400
    first = client.post(path + "/decisiones/revision", headers=review_headers, json=data)
    replay = client.post(path + "/decisiones/revision", headers=review_headers, json=data)
    assert (first.status_code, replay.status_code) == (201, 200)
    assert first.get_json() == replay.get_json()
    assert first.get_json()["actor"] == {"tipo": "revisor", "id": str(admin_id)}
    conflict = client.post(
        path + "/decisiones/revision",
        headers=review_headers,
        json={**data, "resultado": "rechazado"},
    )
    assert conflict.status_code == 409
    assert conflict.get_json()["code"] == "idempotencia_conflictiva"
    current = client.get(path + "/decisiones", headers=headers)
    assert current.get_json()["estado"] == "aprobado"
    assert current.get_json()["asignacionActiva"] is None
    assert (
        client.get(
            "/v1/administracion/colas-revision/revision/asignaciones", headers=headers
        ).get_json()["asignaciones"]
        == []
    )
    with Session(claims.extensions["siniestros_engine"]) as session:
        assert session.scalar(select(func.count()).select_from(DecisionSiniestroModel)) == 2
        assert session.scalar(
            select(Audit.actor).where(Audit.action == "revisar_siniestro")
        ) == str(admin_id)
    customer = claims.test_client().get(
        f"/v1/siniestros/{claim_id}/estado",
        headers={"Authorization": f"Bearer {INTERNAL_TOKEN}", "X-Cliente-ID": str(owner)},
    )
    assert customer.get_json()["estado"] == "aprobado"
    assert INTERNAL_TOKEN not in json.dumps(current.get_json())
    assert current.headers["Cache-Control"] == "no-store"


def test_sesion_cerrada_no_conserva_acceso_administrativo(runtime: Runtime) -> None:
    client, _, _, tokens, _ = runtime
    headers = {"Authorization": f"Bearer {tokens['accessToken']}"}
    assert client.get("/v1/sesiones/actual", headers=headers).status_code == 200
    assert (
        client.delete(
            "/v1/sesiones/actual", headers=headers, json={"refreshToken": tokens["refreshToken"]}
        ).status_code
        == 204
    )
    denied = client.get("/v1/administracion/colas-revision/revision/asignaciones", headers=headers)
    assert denied.status_code == 401


def test_descarga_verifica_evidencia_y_audita_al_administrador(runtime: Runtime) -> None:
    client, claims, _, tokens, admin_id = runtime
    claim_id, owner = pending_claim(claims)
    evidence_id = uuid4()
    content = b"%PDF-1.4 evidencia sintetica para consulta"
    repo = claims.extensions["siniestros_repository"]
    with repo.write() as session:
        upload = Upload(
            id=str(evidence_id),
            claim_id=str(claim_id),
            owner=str(owner),
            operation=str(uuid4()),
            fingerprint="f" * 64,
            mime="application/pdf",
            size=len(content),
            digest=hashlib.sha256(content).hexdigest(),
            location=None,
            captured=None,
            offset=len(content),
            status="confirmada",
            created=datetime.now(UTC).isoformat(),
            confirmed=datetime.now(UTC).isoformat(),
        )
        session.add(upload)
        repo.path(upload).write_bytes(content)
    headers = {"Authorization": f"Bearer {tokens['accessToken']}"}
    path = f"/v1/administracion/siniestros/{claim_id}/evidencias/{evidence_id}/contenido"
    response = client.get(path, headers=headers)
    assert response.status_code == 200
    assert response.data == content
    assert response.mimetype == "application/pdf"
    assert response.headers["Content-Length"] == str(len(content))
    with repo.sessions() as session:
        assert session.scalar(
            select(Audit.actor).where(Audit.action == "consultar_contenido")
        ) == str(admin_id)
    wrong = client.get(path.replace(str(claim_id), str(uuid4())), headers=headers)
    assert wrong.status_code == 404
    repo.path(upload).write_bytes(b"contenido alterado")
    assert client.get(path, headers=headers).status_code == 503
