"""Evidencia confirmada en S3: el directorio local del pod solo sirve de staging."""

import base64
import hashlib
import io
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any, cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from botocore.exceptions import ClientError, EndpointConnectionError
from flask import Flask
from flask.testing import FlaskClient
from PIL import Image
from siniestros import storage
from siniestros.app import create_app
from siniestros.domain import Config as ClaimsConfig
from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.models import Claim
from siniestros.repository import fingerprint
from sqlalchemy import event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

TOKEN = "siniestros-test-internal"
BUCKET = "solventa-test-evidencias"
REPORTE = datetime(2026, 11, 1, 15, 0, tzinfo=UTC)


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.failing: set[str] = set()

    def check(self, operation: str, bucket: str) -> None:
        assert bucket == BUCKET
        if operation in self.failing:
            raise EndpointConnectionError(endpoint_url="https://s3.us-east-2.amazonaws.com")

    def put_object(self, *, Bucket: str, Key: str, Body: IO[bytes], ChecksumSHA256: str) -> object:
        self.check("put_object", Bucket)
        data = Body.read()
        # S3 rejects the PUT when the declared checksum does not match the received bytes.
        if base64.b64encode(hashlib.sha256(data).digest()).decode() != ChecksumSHA256:
            raise ClientError({"Error": {"Code": "BadDigest"}}, "PutObject")
        self.objects[Key] = data
        return {}

    def get_object(self, *, Bucket: str, Key: str) -> Mapping[str, Any]:
        self.check("get_object", Bucket)
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[Key])}

    def delete_object(self, *, Bucket: str, Key: str) -> object:
        self.check("delete_object", Bucket)
        self.objects.pop(Key, None)
        return {}


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> FakeS3:
    fake = FakeS3()
    monkeypatch.setattr(storage, "s3_client", lambda: fake)
    return fake


@pytest.fixture
def pod(tmp_path: Path, s3: FakeS3) -> Iterator[Callable[[], Flask]]:
    """Each call starts a new pod: same database and bucket, empty staging directory."""
    url = f"sqlite:///{tmp_path / 'siniestros.db'}"
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    apps: list[Flask] = []

    def start() -> Flask:
        staging = tmp_path / f"staging-{len(apps)}"
        apps.append(
            create_app(
                ClaimsConfig(url, staging, TOKEN, "http://polizas.test", evidence_bucket=BUCKET)
            )
        )
        return apps[-1]

    yield start
    for app in apps:
        app.extensions["siniestros_engine"].dispose()


def staging(app: Flask) -> Path:
    return cast(Path, app.extensions["siniestros_repository"].config.storage_root)


def headers(cliente: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}", "X-Cliente-ID": str(cliente)}


def jpeg() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (16, 16), "blue").save(output, format="JPEG")
    return output.getvalue()


def registrar(app: Flask, cliente: UUID) -> UUID:
    siniestro_id = uuid4()
    payload = {
        "polizaId": str(uuid4()),
        "fechaEvento": (REPORTE - timedelta(days=1)).isoformat(),
        "tipoEvento": "Accidente",
        "descripcion": "Descripcion sintetica del evento.",
        "lugar": "Lugar de prueba",
    }
    with Session(app.extensions["siniestros_engine"]) as session, session.begin():
        session.add(
            Claim(
                id=str(siniestro_id),
                owner=str(cliente),
                operation=str(uuid4()),
                fingerprint=fingerprint(payload),
                payload=payload,
                created=REPORTE.isoformat(),
                tipo="asistido",
                estado="reportado",
                actualizado_en=REPORTE,
            )
        )
        session.flush()
        session.add(
            CambioEstadoModel(
                siniestro_id=str(siniestro_id),
                secuencia=1,
                estado="reportado",
                ocurrido_en=REPORTE,
                visible_cliente=True,
            )
        )
    return siniestro_id


def cargar(
    client: FlaskClient, cliente: UUID, siniestro_id: UUID, data: bytes, mime: str = "image/jpeg"
) -> str:
    response = client.post(
        f"/v1/siniestros/{siniestro_id}/cargas",
        json={
            "mimeType": mime,
            "tamanoBytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
        headers={**headers(cliente), "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 201, response.get_json()
    upload_id = str(response.get_json()["id"])
    response = client.put(
        f"/v1/siniestros/{siniestro_id}/cargas/{upload_id}/contenido",
        data=data,
        headers={
            **headers(cliente),
            "Content-Type": "application/octet-stream",
            "Upload-Offset": "0",
        },
    )
    assert response.status_code == 200, response.get_json()
    return upload_id


def test_confirmacion_publica_en_s3_y_otro_pod_entrega_el_contenido(
    pod: Callable[[], Flask], s3: FakeS3
) -> None:
    app, cliente, data = pod(), uuid4(), jpeg()
    siniestro_id = registrar(app, cliente)
    upload_id = cargar(app.test_client(), cliente, siniestro_id, data)

    response = app.test_client().post(
        f"/v1/siniestros/{siniestro_id}/cargas/{upload_id}/confirmacion",
        headers=headers(cliente),
    )

    assert response.status_code == 200
    assert response.get_json()["estado"] == "confirmada"
    assert s3.objects == {f"evidencias/{siniestro_id}/{upload_id}": data}
    assert list(staging(app).iterdir()) == []
    nuevo = pod()
    with nuevo.test_client().get(
        f"/v1/siniestros/{siniestro_id}/evidencias/{upload_id}/contenido",
        headers=headers(cliente),
    ) as descarga:
        assert descarga.status_code == 200
        assert descarga.get_data() == data
        assert descarga.mimetype == "image/jpeg"
        assert descarga.headers["Content-Length"] == str(len(data))
    assert list(staging(nuevo).iterdir()) == []


def test_fallo_de_s3_al_confirmar_deja_la_carga_pendiente_y_reintentable(
    pod: Callable[[], Flask], s3: FakeS3
) -> None:
    app, cliente, data = pod(), uuid4(), jpeg()
    client = app.test_client()
    siniestro_id = registrar(app, cliente)
    upload_id = cargar(client, cliente, siniestro_id, data)
    ruta = f"/v1/siniestros/{siniestro_id}/cargas/{upload_id}"
    s3.failing.add("put_object")

    response = client.post(ruta + "/confirmacion", headers=headers(cliente))

    assert response.status_code == 503
    assert response.get_json()["code"] == "servicio_no_disponible"
    assert response.headers["Retry-After"] == "5"
    carga = client.get(ruta, headers=headers(cliente)).get_json()
    assert (carga["estado"], carga["offset"]) == ("pendiente", len(data))
    assert s3.objects == {}
    s3.failing.clear()
    response = client.post(ruta + "/confirmacion", headers=headers(cliente))
    assert response.status_code == 200
    assert response.get_json()["estado"] == "confirmada"
    assert s3.objects == {f"evidencias/{siniestro_id}/{upload_id}": data}


@pytest.mark.parametrize("desenlace", ["reintento", "cancelacion"])
def test_commit_fallido_tras_publicar_no_deja_evidencia_oculta(
    pod: Callable[[], Flask], s3: FakeS3, desenlace: str
) -> None:
    app, cliente, data = pod(), uuid4(), jpeg()
    client = app.test_client()
    siniestro_id = registrar(app, cliente)
    upload_id = cargar(client, cliente, siniestro_id, data)
    ruta = f"/v1/siniestros/{siniestro_id}/cargas/{upload_id}"
    clave = f"evidencias/{siniestro_id}/{upload_id}"
    engine = app.extensions["siniestros_engine"]

    def interrumpir(_: Connection) -> None:
        raise OSError("commit interrumpido")

    event.listen(engine, "commit", interrumpir)
    try:
        assert client.post(ruta + "/confirmacion", headers=headers(cliente)).status_code == 503
    finally:
        event.remove(engine, "commit", interrumpir)

    assert s3.objects == {clave: data}
    assert client.get(ruta, headers=headers(cliente)).get_json()["estado"] == "pendiente"
    if desenlace == "reintento":
        response = client.post(ruta + "/confirmacion", headers=headers(cliente))
        assert response.get_json()["estado"] == "confirmada"
        assert s3.objects == {clave: data}
    else:
        assert client.delete(ruta, headers=headers(cliente)).status_code == 204
        assert s3.objects == {}
        assert list(staging(app).iterdir()) == []


def test_cancelacion_con_s3_caido_conserva_la_carga_hasta_borrar_ambas_copias(
    pod: Callable[[], Flask], s3: FakeS3
) -> None:
    app, cliente, data = pod(), uuid4(), jpeg()
    client = app.test_client()
    siniestro_id = registrar(app, cliente)
    upload_id = cargar(client, cliente, siniestro_id, data)
    ruta = f"/v1/siniestros/{siniestro_id}/cargas/{upload_id}"
    s3.failing.add("delete_object")

    response = client.delete(ruta, headers=headers(cliente))

    assert response.status_code == 503
    carga = client.get(ruta, headers=headers(cliente)).get_json()
    assert (carga["estado"], carga["offset"]) == ("pendiente", len(data))
    assert (staging(app) / upload_id).exists()
    s3.failing.clear()
    assert client.delete(ruta, headers=headers(cliente)).status_code == 204
    assert not (staging(app) / upload_id).exists()
    assert client.get(ruta, headers=headers(cliente)).get_json()["estado"] == "cancelada"


def test_bytes_invalidos_se_borran_sin_publicarse(pod: Callable[[], Flask], s3: FakeS3) -> None:
    app, cliente = pod(), uuid4()
    client = app.test_client()
    siniestro_id = registrar(app, cliente)
    upload_id = cargar(client, cliente, siniestro_id, jpeg(), "image/png")
    ruta = f"/v1/siniestros/{siniestro_id}/cargas/{upload_id}"

    response = client.post(ruta + "/confirmacion", headers=headers(cliente))

    assert response.status_code == 422
    assert response.get_json()["code"] == "formato_invalido"
    assert s3.objects == {}
    assert not (staging(app) / upload_id).exists()
    assert client.get(ruta, headers=headers(cliente)).get_json()["offset"] == 0


@pytest.mark.parametrize("falla", ["ausente", "alterada", "s3_caido"])
def test_copia_remota_no_integra_o_inaccesible_no_se_entrega(
    pod: Callable[[], Flask], s3: FakeS3, falla: str
) -> None:
    app, cliente, data = pod(), uuid4(), jpeg()
    client = app.test_client()
    siniestro_id = registrar(app, cliente)
    upload_id = cargar(client, cliente, siniestro_id, data)
    confirmacion = f"/v1/siniestros/{siniestro_id}/cargas/{upload_id}/confirmacion"
    assert client.post(confirmacion, headers=headers(cliente)).status_code == 200
    clave = f"evidencias/{siniestro_id}/{upload_id}"
    if falla == "ausente":
        del s3.objects[clave]
    elif falla == "alterada":
        s3.objects[clave] = data[:-1] + b"\x00"
    else:
        s3.failing.add("get_object")

    with client.get(
        f"/v1/siniestros/{siniestro_id}/evidencias/{upload_id}/contenido",
        headers=headers(cliente),
    ) as response:
        assert response.status_code == 503
        assert response.get_json()["code"] == "servicio_no_disponible"
        assert data not in response.get_data()
    assert list(staging(app).iterdir()) == []


@pytest.mark.parametrize(
    "bucket", ["Mayusculas", "ab", "guion_bajo", "doble..punto", "-inicio", "fin-", "x" * 64]
)
def test_nombre_de_bucket_invalido_se_rechaza(tmp_path: Path, bucket: str) -> None:
    with pytest.raises(ValueError, match="SINIESTROS_EVIDENCIAS_BUCKET"):
        ClaimsConfig(
            f"sqlite:///{tmp_path / 'siniestros.db'}",
            tmp_path,
            TOKEN,
            "http://polizas.test",
            evidence_bucket=bucket,
        )


def test_bucket_del_entorno_activa_s3_y_vacio_conserva_disco_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, s3: FakeS3
) -> None:
    monkeypatch.setenv("SINIESTROS_DATABASE_URL", f"sqlite:///{tmp_path / 'siniestros.db'}")
    monkeypatch.setenv("SINIESTROS_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("INTERNAL_API_TOKEN", TOKEN)
    monkeypatch.setenv("POLIZAS_SERVICE_URL", "http://polizas.test")
    monkeypatch.setenv("SINIESTROS_EVIDENCIAS_BUCKET", f" {BUCKET} ")

    config = ClaimsConfig.from_environment()

    assert config.evidence_bucket == BUCKET
    assert isinstance(storage.evidence_store(config), storage.S3Evidence)
    monkeypatch.setenv("SINIESTROS_EVIDENCIAS_BUCKET", " ")
    local = ClaimsConfig.from_environment()
    assert local.evidence_bucket is None
    assert isinstance(storage.evidence_store(local), storage.LocalEvidence)


def test_cliente_s3_usa_la_region_del_pod_y_falla_rapido(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_REGION", "us-east-2")

    client = cast(Any, storage.s3_client())

    assert client.meta.region_name == "us-east-2"
    assert (client.meta.config.connect_timeout, client.meta.config.read_timeout) == (3, 10)
