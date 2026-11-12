import hashlib
import io
import socket
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import av
import pytest
from bff_movil.app import create_app as create_mobile
from bff_web.app import create_app as create_web
from PIL import Image, PngImagePlugin
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from siniestros.app import create_app
from siniestros.domain import ClaimInput, Config, Problem, UploadInput
from siniestros.infrastructure.models import CambioEstadoModel
from siniestros.models import Audit, Claim, Upload
from siniestros.repository import Repository
from sqlalchemy import event, func, select
from sqlalchemy.engine import Connection

from tests.claims_local_stack import call, stack


@pytest.fixture(scope="module")
def runtime(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, Any]]:
    with stack(tmp_path_factory.mktemp("claims-evidence"), max_count=100) as running:
        yield running


def image_bytes(format: str = "JPEG", gps: bool = False) -> bytes:
    output = io.BytesIO()
    image = Image.new("RGB", (16, 16), "blue")
    kwargs: dict[str, Any] = {}
    if gps and format == "JPEG":
        exif = Image.Exif()
        exif[34853] = {1: "N", 2: (4.0, 0.0, 0.0), 3: "W", 4: (74.0, 0.0, 0.0)}
        kwargs["exif"] = exif
    if gps and format == "PNG":
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("GPSLatitude", "4")
        kwargs["pnginfo"] = metadata
    image.save(output, format=format, **kwargs)
    return output.getvalue()


def pdf_bytes(gps: bool = False) -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    if gps:
        writer.add_metadata({"/GPSLatitude": "4"})
    writer.write(output)
    return output.getvalue()


def video_bytes(tmp_path: Path, gps: bool = False, frame_count: int = 2) -> bytes:
    path = tmp_path / (uuid4().hex + ".mp4")
    with av.open(str(path), mode="w") as container:
        if gps:
            container.metadata["location"] = "+04.0000-074.0000/"
        stream = container.add_stream("mpeg4", rate=10)
        stream.width = stream.height = 16
        stream.pix_fmt = "yuv420p"
        for _ in range(frame_count):
            frame = av.VideoFrame.from_image(Image.new("RGB", (16, 16), "green"))
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path.read_bytes()


def start_upload(
    runtime: dict, data: bytes, mime: str = "image/jpeg", **extra: Any
) -> tuple[str, str, dict]:
    user = runtime["users"][0]
    root = runtime["mobile"] + "/siniestros/" + user["claim"]["id"]
    body = {"mimeType": mime, "tamanoBytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    body.update(extra)
    status, upload = call(
        root + "/cargas", "POST", body, user["token"], {"Idempotency-Key": str(uuid4())}
    )
    assert status == 201, upload
    return root, root + "/cargas/" + upload["id"], upload


def put(runtime: dict, url: str, data: bytes, offset: int = 0) -> tuple[int, Any]:
    return call(
        url + "/contenido",
        "PUT",
        data,
        runtime["users"][0]["token"],
        {
            "Content-Type": "application/octet-stream",
            "Upload-Offset": str(offset),
        },
    )


def test_real_identity_policies_web_mobile_ownership_and_claim_replays(runtime: dict) -> None:
    first, second = runtime["users"]
    url = runtime["mobile"] + "/siniestros"
    status, data = call(url, token=first["token"])
    assert status == 200 and [c["id"] for c in data["siniestros"]] == [first["claim"]["id"]]
    assert call(url + "/" + first["claim"]["id"], token=second["token"])[0] == 404
    assert call(url)[0] == 401
    status, policies = call(url + "/polizas", token=first["token"])
    assert status == 200 and policies["polizas"][0]["id"] == first["claim"]["polizaId"]
    status, record = call(
        runtime["web"] + "/siniestros/" + first["claim"]["id"], token=first["webToken"]
    )
    assert status == 200 and record == first["claim"]
    assert call(runtime["web"] + "/siniestros", token=first["token"])[0] == 401
    headers = {"Idempotency-Key": first["claimOperation"]}
    assert call(url, "POST", first["claimPayload"], first["token"], headers) == (
        200,
        first["claim"],
    )
    status, problem = call(
        url, "POST", {**first["claimPayload"], "lugar": "Otro lugar"}, first["token"], headers
    )
    assert (status, problem["code"]) == (409, "idempotencia_conflictiva")
    status, problem = call(
        url,
        "POST",
        {
            **first["claimPayload"],
            "polizaId": second["claim"]["polizaId"],
        },
        first["token"],
        {"Idempotency-Key": str(uuid4())},
    )
    assert (status, problem["code"]) == (404, "poliza_no_encontrada")


def test_registered_claim_has_shared_status_without_invented_classification(runtime: dict) -> None:
    first, second = runtime["users"]
    claim_id = first["claim"]["id"]
    web = runtime["web"] + "/clientes/me/siniestros/" + claim_id
    mobile = runtime["mobile"] + "/siniestros/" + claim_id + "/estado"
    status, result = call(web, token=first["webToken"])
    assert status == 200
    assert result["id"] == claim_id and result["tipo"] is None
    assert result["estado"] == "reportado"
    assert len(result["historial"]) == 1
    assert result["historial"][0]["estado"] == "reportado"
    assert result["actualizadoEn"] == result["reportadoEn"]
    assert datetime.fromisoformat(result["reportadoEn"]) == datetime.fromisoformat(
        first["claim"]["registradoEn"]
    )
    assert call(mobile, token=first["token"]) == (200, result)
    assert call(web, token=second["webToken"])[0] == 404
    assert call(mobile, token=second["token"])[0] == 404
    assert call(
        runtime["mobile"] + "/siniestros",
        "POST",
        first["claimPayload"],
        first["token"],
        {"Idempotency-Key": first["claimOperation"]},
    ) == (200, first["claim"])
    assert call(web, token=first["webToken"]) == (200, result)


@pytest.mark.parametrize("kind", ["jpeg", "png", "pdf", "mp4"])
def test_real_formats_durable_confirmation_lost_ack_and_private_download(
    runtime: dict, tmp_path: Path, kind: str
) -> None:
    generators: dict[str, Callable[[], bytes]] = {
        "jpeg": lambda: image_bytes(),
        "png": lambda: image_bytes("PNG"),
        "pdf": pdf_bytes,
        "mp4": lambda: video_bytes(tmp_path),
    }
    data = generators[kind]()
    mime = {"jpeg": "image/jpeg", "png": "image/png", "pdf": "application/pdf", "mp4": "video/mp4"}[
        kind
    ]
    first, second = runtime["users"]
    root, url, upload = start_upload(runtime, data, mime)
    status, problem = call(url + "/confirmacion", "POST", token=first["token"])
    assert (status, problem["code"]) == (409, "carga_incompleta")
    split = len(data) // 2
    assert put(runtime, url, data[:split])[0] == 200
    assert call(url, token=first["token"])[1]["offset"] == split
    assert put(runtime, url, data[:split])[1]["code"] == "offset_conflictivo"
    assert put(runtime, url, data[split:], split)[0] == 200
    status, result = call(url + "/confirmacion", "POST", token=first["token"])
    assert status == 200 and result["estado"] == "confirmada"
    assert call(url + "/confirmacion", "POST", token=first["token"]) == (200, result)
    assert call(url, token=first["token"])[1]["estado"] == "confirmada"
    download = root + "/evidencias/" + upload["id"] + "/contenido"
    assert call(download, token=first["token"]) == (200, data)
    assert call(download, token=second["token"])[0] == 404
    assert call(url, token=second["token"])[0] == 404
    assert put(runtime, url, data)[1]["code"] == "carga_confirmada"
    assert call(url, "DELETE", token=first["token"])[1]["code"] == "carga_confirmada"
    restarted = create_app(runtime["config"])
    headers = {"Authorization": f"Bearer {runtime['internal']}", "X-Cliente-ID": first["id"]}
    route = "/v1/siniestros/" + first["claim"]["id"] + "/cargas/" + upload["id"]
    assert restarted.test_client().get(route, headers=headers).get_json()["estado"] == "confirmada"
    restarted.extensions["siniestros_repository"].engine.dispose()


@pytest.mark.parametrize("kind", ["jpeg", "png", "pdf", "mp4"])
def test_embedded_geo_requires_consent_and_rejection_erases_bytes(
    runtime: dict, tmp_path: Path, kind: str
) -> None:
    data = {
        "jpeg": lambda: image_bytes(gps=True),
        "png": lambda: image_bytes("PNG", gps=True),
        "pdf": lambda: pdf_bytes(gps=True),
        "mp4": lambda: video_bytes(tmp_path, gps=True),
    }[kind]()
    mime = {"jpeg": "image/jpeg", "png": "image/png", "pdf": "application/pdf", "mp4": "video/mp4"}[
        kind
    ]
    root, url, upload = start_upload(runtime, data, mime)
    assert put(runtime, url, data)[0] == 200
    token = runtime["users"][0]["token"]
    status, problem = call(url + "/confirmacion", "POST", token=token)
    assert (status, problem["code"]) == (422, "ubicacion_no_autorizada")
    assert call(url, token=token)[1]["offset"] == 0
    assert not (runtime["config"].storage_root / upload["id"]).exists()
    assert all(
        e["id"] != upload["id"] for e in call(root + "/evidencias", token=token)[1]["evidencias"]
    )
    location = {
        "consentimiento": True,
        "latitud": 4,
        "longitud": -74,
        "fecha": datetime.now(UTC).isoformat(),
    }
    _, allowed, _ = start_upload(runtime, data, mime, ubicacion=location)
    assert put(runtime, allowed, data)[0] == 200
    assert call(allowed + "/confirmacion", "POST", token=token)[0] == 200


@pytest.mark.parametrize("channel", ["mobile", "web"])
def test_pdf_with_innocent_structured_metadata_is_accepted(runtime: dict, channel: str) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_metadata(
        {
            "/ecv-data": '{"location":{"city":"Bogota"},"showLocation":true,"scores":[-74.0,4.6]}',
            "/Title": "GPS and location in a travel document",
        }
    )
    metadata = DecodedStreamObject()
    metadata.set_data(
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        b"<dc:description>GPS location and latitude are ordinary words here</dc:description>"
        b"</x:xmpmeta>"
    )
    writer._root_object[NameObject("/Metadata")] = writer._add_object(metadata)
    output = io.BytesIO()
    writer.write(output)
    data = output.getvalue()
    first = runtime["users"][0]
    token = first["token"] if channel == "mobile" else first["webToken"]
    root = runtime[channel] + "/siniestros/" + first["claim"]["id"]
    payload = {
        "mimeType": "application/pdf",
        "tamanoBytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    status, upload = call(
        root + "/cargas", "POST", payload, token, {"Idempotency-Key": str(uuid4())}
    )
    assert status == 201
    url = root + "/cargas/" + upload["id"]
    assert (
        call(
            url + "/contenido",
            "PUT",
            data,
            token,
            {"Content-Type": "application/octet-stream", "Upload-Offset": "0"},
        )[0]
        == 200
    )
    assert call(url + "/confirmacion", "POST", token=token)[0] == 200
    assert call(root + "/evidencias/" + upload["id"] + "/contenido", token=token) == (200, data)


@pytest.mark.parametrize("kind", ["png", "jpeg"])
def test_image_with_innocent_text_metadata_is_accepted(runtime: dict, kind: str) -> None:
    output = io.BytesIO()
    image = Image.new("RGB", (16, 16), "blue")
    if kind == "png":
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Description", "GPS location and latitude are ordinary words")
        image.save(output, format="PNG", pnginfo=metadata)
    else:
        exif = Image.Exif()
        exif[270] = "GPS location and latitude are ordinary words"
        image.save(output, format="JPEG", exif=exif)
    data = output.getvalue()
    _, url, _ = start_upload(runtime, data, f"image/{kind}")
    assert put(runtime, url, data)[0] == 200
    assert call(url + "/confirmacion", "POST", token=runtime["users"][0]["token"])[0] == 200


@pytest.mark.parametrize(
    "xmp",
    [
        b"<exif:GPSLatitude>4,30N</exif:GPSLatitude>",
        b'<rdf:Description exif:GPSLongitude="74,00W"/>',
        b"<exif:Location><exif:coordinates>-74.0,4.6</exif:coordinates></exif:Location>",
    ],
)
def test_xmp_coordinates_require_consent(runtime: dict, xmp: bytes) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    metadata = DecodedStreamObject()
    metadata.set_data(
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:exif="http://ns.adobe.com/exif/1.0/" '
        b'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">' + xmp + b"</x:xmpmeta>"
    )
    writer._root_object[NameObject("/Metadata")] = writer._add_object(metadata)
    output = io.BytesIO()
    writer.write(output)
    data = output.getvalue()
    _, url, _ = start_upload(runtime, data, "application/pdf")
    assert put(runtime, url, data)[0] == 200
    token = runtime["users"][0]["token"]
    assert call(url + "/confirmacion", "POST", token=token)[1]["code"] == "ubicacion_no_autorizada"
    location = {
        "consentimiento": True,
        "latitud": 4,
        "longitud": -74,
        "fecha": datetime.now(UTC).isoformat(),
    }
    _, allowed, _ = start_upload(runtime, data, "application/pdf", ubicacion=location)
    assert put(runtime, allowed, data)[0] == 200
    assert call(allowed + "/confirmacion", "POST", token=token)[0] == 200


@pytest.mark.parametrize(
    "location",
    [
        '{"location":"+04.0000-074.0000/"}',
        '{"location":{"lat":4.0,"lng":-74.0}}',
        '{"location":[4.0,-74.0]}',
    ],
)
def test_pdf_structured_location_coordinates_require_consent(runtime: dict, location: str) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_metadata({"/ecv-data": location})
    output = io.BytesIO()
    writer.write(output)
    data = output.getvalue()
    _, url, _ = start_upload(runtime, data, "application/pdf")
    assert put(runtime, url, data)[0] == 200
    status, problem = call(url + "/confirmacion", "POST", token=runtime["users"][0]["token"])
    assert (status, problem["code"]) == (422, "ubicacion_no_autorizada")


@pytest.mark.parametrize(
    "metadata",
    [
        '{"location":{"coordinates":[-74.0,4.6]}}',
        '{"location":{"places":[{"coordinates":[-74.0,4.6]}]}}',
        '{"location":{"coordinates":"-74.0,4.6"}}',
    ],
)
def test_nested_location_coordinates_require_consent(runtime: dict, metadata: str) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_metadata({"/ecv-data": metadata})
    output = io.BytesIO()
    writer.write(output)
    data = output.getvalue()
    root, url, upload = start_upload(runtime, data, "application/pdf")
    repo = runtime["app"].extensions["siniestros_repository"]
    with repo.sessions() as session:
        assert session.get(Upload, upload["id"]).location is None
    assert put(runtime, url, data)[0] == 200
    token = runtime["users"][0]["token"]
    status, problem = call(url + "/confirmacion", "POST", token=token)
    assert (status, problem.get("code")) == (422, "ubicacion_no_autorizada")
    assert all(
        item["id"] != upload["id"]
        for item in call(root + "/evidencias", token=token)[1]["evidencias"]
    )
    location = {
        "consentimiento": True,
        "latitud": 4.6,
        "longitud": -74,
        "fecha": datetime.now(UTC).isoformat(),
    }
    _, allowed, _ = start_upload(runtime, data, "application/pdf", ubicacion=location)
    assert put(runtime, allowed, data)[0] == 200
    assert call(allowed + "/confirmacion", "POST", token=token)[0] == 200


@pytest.mark.parametrize("kind", ["pdf", "png"])
@pytest.mark.parametrize(
    "comment,expected",
    [("location: 4.7,-74.0", (422, "ubicacion_no_autorizada")), ("location: Bogota", (200, None))],
)
def test_plain_metadata_location_distinguishes_coordinates_from_city(
    runtime: dict, kind: str, comment: str, expected: tuple[int, str | None]
) -> None:
    output = io.BytesIO()
    if kind == "pdf":
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.add_metadata({"/Subject": comment})
        writer.write(output)
    else:
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Comment", comment)
        Image.new("RGB", (16, 16), "blue").save(output, format="PNG", pnginfo=metadata)
    data = output.getvalue()
    _, url, _ = start_upload(runtime, data, "application/pdf" if kind == "pdf" else "image/png")
    assert put(runtime, url, data)[0] == 200
    status, result = call(url + "/confirmacion", "POST", token=runtime["users"][0]["token"])
    assert (status, result.get("code")) == expected
    if expected[0] == 422:
        location = {
            "consentimiento": True,
            "latitud": 4.7,
            "longitud": -74,
            "fecha": datetime.now(UTC).isoformat(),
        }
        _, allowed, _ = start_upload(
            runtime,
            data,
            "application/pdf" if kind == "pdf" else "image/png",
            ubicacion=location,
        )
        assert put(runtime, allowed, data)[0] == 200
        assert call(allowed + "/confirmacion", "POST", token=runtime["users"][0]["token"])[0] == 200


def test_utf16_xmp_coordinates_require_consent(runtime: dict) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    metadata = DecodedStreamObject()
    metadata.set_data(
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:exif="http://ns.adobe.com/exif/1.0/">'
        "<exif:GPSLatitude>4,30N</exif:GPSLatitude></x:xmpmeta>".encode("utf-16")
    )
    writer._root_object[NameObject("/Metadata")] = writer._add_object(metadata)
    output = io.BytesIO()
    writer.write(output)
    data = output.getvalue()
    _, url, _ = start_upload(runtime, data, "application/pdf")
    assert put(runtime, url, data)[0] == 200
    status, problem = call(url + "/confirmacion", "POST", token=runtime["users"][0]["token"])
    assert (status, problem["code"]) == (422, "ubicacion_no_autorizada")


def test_xmp_doctype_is_rejected_without_expanding_entities(runtime: dict) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    metadata = DecodedStreamObject()
    metadata.set_data(b'<!DOCTYPE x [<!ENTITY a "data">]><x>&a;</x>')
    writer._root_object[NameObject("/Metadata")] = writer._add_object(metadata)
    output = io.BytesIO()
    writer.write(output)
    data = output.getvalue()
    _, url, _ = start_upload(runtime, data, "application/pdf")
    assert put(runtime, url, data)[0] == 200
    status, problem = call(url + "/confirmacion", "POST", token=runtime["users"][0]["token"])
    assert (status, problem["code"]) == (422, "formato_invalido")


def test_invalid_pdf_is_rejected(runtime: dict) -> None:
    data = b"%PDF-1.7\ninvalid synthetic document"
    _, url, _ = start_upload(runtime, data, "application/pdf")
    assert put(runtime, url, data)[0] == 200
    status, problem = call(url + "/confirmacion", "POST", token=runtime["users"][0]["token"])
    assert (status, problem["code"]) == (422, "formato_invalido")


@pytest.mark.parametrize("channel", ["mobile", "web"])
def test_protected_pdf_reject_reconcile_cancel_and_replace(runtime: dict, channel: str) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("synthetic-password")
    output = io.BytesIO()
    writer.write(output)
    data = output.getvalue()
    first = runtime["users"][0]
    token = first["token"] if channel == "mobile" else first["webToken"]
    root = runtime[channel] + "/siniestros/" + first["claim"]["id"]

    def create(content: bytes) -> str:
        body = {
            "mimeType": "application/pdf",
            "tamanoBytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        status, upload = call(
            root + "/cargas", "POST", body, token, {"Idempotency-Key": str(uuid4())}
        )
        assert status == 201
        url = root + "/cargas/" + upload["id"]
        assert (
            call(
                url + "/contenido",
                "PUT",
                content,
                token,
                {"Content-Type": "application/octet-stream", "Upload-Offset": "0"},
            )[0]
            == 200
        )
        return url

    url = create(data)
    status, problem = call(url + "/confirmacion", "POST", token=token)
    assert (status, problem["code"]) == (422, "formato_invalido")
    status, upload = call(url, token=token)
    assert status == 200 and upload["estado"] == "pendiente" and upload["offset"] == 0
    assert upload["ultimoResultado"]["code"] == "formato_invalido"
    assert call(url, "DELETE", token=token)[0] == 204
    replacement = create(pdf_bytes())
    assert call(replacement + "/confirmacion", "POST", token=token)[0] == 200


@pytest.mark.parametrize(
    "data,digest,code",
    [
        (b"not an image", None, "formato_invalido"),
        (image_bytes()[:-30], None, "formato_invalido"),
        (image_bytes(), "0" * 64, "integridad_invalida"),
    ],
)
def test_corrupt_mismatched_hash_never_confirms(
    runtime: dict, data: bytes, digest: str | None, code: str
) -> None:
    kwargs = {"sha256": digest} if digest else {}
    _, url, _ = start_upload(runtime, data, **kwargs)
    assert put(runtime, url, data)[0] == 200
    status, problem = call(url + "/confirmacion", "POST", token=runtime["users"][0]["token"])
    assert (status, problem["code"]) == (422, code)


def test_concurrent_initialization_and_quota_include_pending(runtime: dict) -> None:
    config = replace(runtime["config"], max_count=1)
    app = create_app(config)
    repo = app.extensions["siniestros_repository"]
    first = runtime["users"][0]
    claim, _ = repo.create_claim(
        first["id"], str(uuid4()), ClaimInput.model_validate(first["claimPayload"]), str(uuid4())
    )
    data = image_bytes()
    payload = UploadInput(
        mimeType="image/jpeg", tamanoBytes=len(data), sha256=hashlib.sha256(data).hexdigest()
    )
    operation = str(uuid4())

    def start(key: str) -> Any:
        try:
            return repo.start(first["id"], claim["id"], key, payload, str(uuid4()))
        except Problem as error:
            return error.status, error.code

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(start, [operation] * 4))
    assert sorted(result[1] for result in results) == [200, 200, 200, 201]
    assert len({result[0]["id"] for result in results}) == 1
    assert start(str(uuid4())) == (409, "limite_cantidad")
    with repo.sessions() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Upload).where(Upload.claim_id == claim["id"])
            )
            == 1
        )
    repo.engine.dispose()


def test_interrupt_and_sql_commit_failure_recover_without_confirming(runtime: dict) -> None:
    data = image_bytes()
    first = runtime["users"][0]
    _, url, upload = start_upload(runtime, data)
    repo = runtime["app"].extensions["siniestros_repository"]
    args = (first["id"], first["claim"]["id"], upload["id"])
    with pytest.raises(Problem, match="carga_interrumpida"):
        repo.append(*args, 0, len(data), io.BytesIO(data[:30]), str(uuid4()))
    assert repo.get_upload(*args)["offset"] == 0

    def fail_commit(connection: Connection) -> None:
        raise OSError("injected SQL acknowledgment failure")

    event.listen(repo.engine, "commit", fail_commit)
    try:
        with pytest.raises(OSError):
            repo.append(*args, 0, len(data), io.BytesIO(data), str(uuid4()))
    finally:
        event.remove(repo.engine, "commit", fail_commit)
    assert repo.get_upload(*args)["offset"] == 0
    assert put(runtime, url, data)[0] == 200
    event.listen(repo.engine, "commit", fail_commit)
    try:
        with pytest.raises(OSError):
            repo.cancel(*args, str(uuid4()))
    finally:
        event.remove(repo.engine, "commit", fail_commit)
    assert repo.get_upload(*args)["offset"] == 0
    assert put(runtime, url, data)[0] == 200
    assert repo.confirm(*args, str(uuid4()))["estado"] == "confirmada"
    with repo.sessions() as session:
        assert session.scalar(select(func.count()).select_from(Audit)) > 0


def _process_start(args: tuple[Config, str, str, str, UploadInput]) -> tuple[int, str]:
    config, owner, claim_id, operation, payload = args
    repository = Repository(config)
    try:
        result, status = repository.start(owner, claim_id, operation, payload, str(uuid4()))
        return status, result["id"]
    except Problem as error:
        return error.status, error.code
    finally:
        repository.engine.dispose()


def test_multi_process_quota_reservation_and_claim_idempotency(runtime: dict) -> None:
    config = replace(runtime["config"], max_count=1)
    first = runtime["users"][0]
    repo = Repository(config)
    body = ClaimInput.model_validate(first["claimPayload"])
    operation = str(uuid4())
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(repo.create_claim, first["id"], operation, body, str(uuid4()))
            for _ in range(4)
        ]
        claims = [f.result() for f in futures]
    assert sorted(result[1] for result in claims) == [200, 200, 200, 201]
    assert len({result[0]["id"] for result in claims}) == 1
    claim_id = claims[0][0]["id"]
    payload = UploadInput(mimeType="image/jpeg", tamanoBytes=100, sha256="0" * 64)
    with ProcessPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                _process_start,
                [(config, first["id"], claim_id, str(uuid4()), payload) for _ in range(2)],
            )
        )
    assert sorted(result[0] for result in results) == [201, 409]
    assert (409, "limite_cantidad") in results
    repo.engine.dispose()


def test_real_socket_interruption_then_resume(runtime: dict) -> None:
    data = image_bytes()
    _, url, upload = start_upload(runtime, data)
    first = runtime["users"][0]
    address = urlsplit(runtime["claims"])
    route = f"/v1/siniestros/{first['claim']['id']}/cargas/{upload['id']}/contenido"
    with socket.create_connection((address.hostname or "", address.port or 80), timeout=5) as conn:
        wire = (
            f"PUT {route} HTTP/1.1\r\nHost: localhost\r\n"
            f"Authorization: Bearer {runtime['internal']}\r\nX-Cliente-ID: {first['id']}\r\n"
            f"Content-Type: application/octet-stream\r\nContent-Length: {len(data)}\r\n"
            "Upload-Offset: 0\r\nConnection: close\r\n\r\n"
        ).encode()
        conn.sendall(wire + data[:30])
        conn.shutdown(socket.SHUT_WR)
        assert b"400" in conn.recv(4096)
    status, record = call(url, token=first["token"])
    assert status == 200 and record["offset"] == 0 and record["estado"] == "pendiente"
    assert put(runtime, url, data)[0] == 200
    assert call(url + "/confirmacion", "POST", token=first["token"])[1]["estado"] == "confirmada"


def test_policy_limits_conflicts_cancel_and_invalid_metadata(runtime: dict) -> None:
    token = runtime["users"][0]["token"]
    claim_id = runtime["users"][0]["claim"]["id"]
    root = runtime["mobile"] + "/siniestros/" + claim_id
    status, config = call(runtime["mobile"] + "/siniestros/configuracion-evidencias", token=token)
    assert status == 200 and config["maxChunkBytes"] == 1024 * 1024
    payload = {"mimeType": "image/jpeg", "tamanoBytes": 100, "sha256": "0" * 64}
    operation = {"Idempotency-Key": str(uuid4())}
    status, record = call(root + "/cargas", "POST", payload, token, operation)
    assert status == 201
    assert call(root + "/cargas", "POST", payload, token, operation) == (200, record)
    status, problem = call(
        root + "/cargas", "POST", {**payload, "tamanoBytes": 101}, token, operation
    )
    assert (status, problem["code"]) == (409, "idempotencia_conflictiva")
    url = root + "/cargas/" + record["id"]
    status, problem = put(runtime, url, b"x" * 101)
    assert (status, problem["code"]) == (413, "limite_tamano")
    assert call(url, token=token)[1]["offset"] == 0
    assert call(url, "DELETE", token=token)[0] == 204
    assert call(url, "DELETE", token=token)[0] == 204
    assert call(url + "/confirmacion", "POST", token=token)[1]["code"] == "carga_cancelada"
    cases: list[tuple[dict[str, Any], tuple[int, str]]] = [
        ({"mimeType": "text/html"}, (415, "formato_no_permitido")),
        ({"tamanoBytes": 0}, (400, "solicitud_invalida")),
        ({"tamanoBytes": 10 * 1024 * 1024 + 1}, (413, "limite_tamano")),
        (
            {
                "ubicacion": {
                    "consentimiento": False,
                    "latitud": 4,
                    "longitud": -74,
                    "fecha": datetime.now(UTC).isoformat(),
                }
            },
            (422, "ubicacion_no_autorizada"),
        ),
        ({"fechaCaptura": 0}, (400, "solicitud_invalida")),
    ]
    for changed, expected in cases:
        status, problem = call(
            root + "/cargas",
            "POST",
            {**payload, **changed},
            token,
            {"Idempotency-Key": str(uuid4())},
        )
        assert (status, problem["code"]) == expected


def test_failed_confirmation_sql_commit_after_unlink_is_recoverable(runtime: dict) -> None:
    data = image_bytes()
    _, url, upload = start_upload(runtime, data, sha256="0" * 64)
    assert put(runtime, url, data)[0] == 200
    first = runtime["users"][0]
    repo = runtime["app"].extensions["siniestros_repository"]

    def fail_commit(connection: Connection) -> None:
        raise OSError("injected SQL confirmation failure")

    event.listen(repo.engine, "commit", fail_commit)
    try:
        with pytest.raises(OSError):
            repo.confirm(first["id"], first["claim"]["id"], upload["id"], str(uuid4()))
    finally:
        event.remove(repo.engine, "commit", fail_commit)
    assert call(url, token=first["token"])[1]["offset"] == 0
    assert put(runtime, url, data)[0] == 200
    assert (
        call(url + "/confirmacion", "POST", token=first["token"])[1]["code"]
        == "integridad_invalida"
    )


def test_exact_byte_chunk_video_limits_and_declared_mime(runtime: dict, tmp_path: Path) -> None:
    data = image_bytes()
    config = replace(runtime["config"], photo_bytes=len(data), max_chunk=32, video_seconds=1)
    app = create_app(config)
    client = app.test_client()
    user = runtime["users"][0]
    root = f"/v1/siniestros/{user['claim']['id']}/cargas"
    headers = {"Authorization": f"Bearer {runtime['internal']}", "X-Cliente-ID": user["id"]}
    payload = {
        "mimeType": "image/jpeg",
        "tamanoBytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    response = client.post(root, json=payload, headers={**headers, "Idempotency-Key": str(uuid4())})
    assert response.status_code == 201
    upload_id = response.get_json()["id"]
    response = client.post(
        root,
        json={**payload, "tamanoBytes": len(data) + 1},
        headers={**headers, "Idempotency-Key": str(uuid4())},
    )
    assert response.status_code == 413
    url = f"{root}/{upload_id}"
    chunk_headers = {**headers, "Content-Type": "application/octet-stream", "Upload-Offset": "0"}
    assert client.put(url + "/contenido", data=data[:33], headers=chunk_headers).status_code == 413
    assert client.get(url, headers=headers).get_json()["offset"] == 0
    for offset in range(0, len(data), 32):
        response = client.put(
            url + "/contenido",
            data=data[offset : offset + 32],
            headers={**chunk_headers, "Upload-Offset": str(offset)},
        )
        assert response.status_code == 200
    assert client.post(url + "/confirmacion", headers=headers).get_json()["estado"] == "confirmada"
    video = video_bytes(tmp_path, frame_count=20)
    _, video_url, video_upload = start_upload(runtime, video, "video/mp4")
    assert put(runtime, video_url, video)[0] == 200
    response = client.post(f"{root}/{video_upload['id']}/confirmacion", headers=headers)
    assert response.status_code == 422 and response.get_json()["code"] == "video_fuera_limite"
    _, mismatched, _ = start_upload(runtime, data, "image/png")
    assert put(runtime, mismatched, data)[0] == 200
    assert (
        call(mismatched + "/confirmacion", "POST", token=user["token"])[1]["code"]
        == "formato_invalido"
    )
    app.extensions["siniestros_repository"].engine.dispose()


def test_valid_confirmation_commit_failure_then_retry_is_single_evidence(runtime: dict) -> None:
    data = image_bytes()
    root, url, upload = start_upload(runtime, data)
    assert put(runtime, url, data)[0] == 200
    first = runtime["users"][0]
    repo = runtime["app"].extensions["siniestros_repository"]

    def fail_commit(connection: Connection) -> None:
        raise OSError("injected valid confirmation failure")

    event.listen(repo.engine, "commit", fail_commit)
    try:
        with pytest.raises(OSError):
            repo.confirm(first["id"], first["claim"]["id"], upload["id"], str(uuid4()))
    finally:
        event.remove(repo.engine, "commit", fail_commit)
    assert call(url, token=first["token"])[1]["estado"] == "pendiente"
    assert all(
        item["id"] != upload["id"]
        for item in call(root + "/evidencias", token=first["token"])[1]["evidencias"]
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: call(url + "/confirmacion", "POST", token=first["token"]), range(2)
            )
        )
    assert all(status == 200 and result["estado"] == "confirmada" for status, result in results)
    items = call(root + "/evidencias", token=first["token"])[1]["evidencias"]
    assert sum(item["id"] == upload["id"] for item in items) == 1


@pytest.mark.parametrize("channel", ["mobile", "web"])
def test_bff_ignores_forged_subject_and_internal_failure_is_not_logout(
    runtime: dict, channel: str
) -> None:
    user, other = runtime["users"]
    token = user["token"] if channel == "mobile" else user["webToken"]
    status, records = call(
        runtime[channel] + "/siniestros",
        token=token,
        headers={"X-Cliente-ID": other["id"]},
    )
    assert status == 200 and other["claim"]["id"] not in [r["id"] for r in records["siniestros"]]
    original_config = runtime[channel + "App"].extensions["bff_config"]
    create = create_mobile if channel == "mobile" else create_web
    app = create(replace(original_config, internal_api_token="deliberately-invalid-local-test"))
    prefix = "/movil/v1" if channel == "mobile" else "/v1"
    response = app.test_client().get(
        prefix + "/siniestros", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 503
    assert response.get_json()["code"] == "servicio_no_disponible"


def test_changed_mime_policy_rejects_pending_bytes_before_storage(runtime: dict) -> None:
    data = image_bytes()
    _, _, upload = start_upload(runtime, data)
    user = runtime["users"][0]
    config = replace(runtime["config"], allowed_mimes=("application/pdf",))
    repo = Repository(config)
    assert config.public()["formatos"] == [{"mimeType": "application/pdf", "maxBytes": 20971520}]
    with pytest.raises(Problem, match="formato_no_permitido"):
        repo.append(
            user["id"],
            user["claim"]["id"],
            upload["id"],
            0,
            len(data),
            io.BytesIO(data),
            str(uuid4()),
        )
    assert not (config.storage_root / upload["id"]).exists()
    repo.engine.dispose()


def _process_read_confirmed(args: tuple[Config, str, str, str]) -> tuple[str, str]:
    config, owner, claim_id, upload_id = args
    repo = Repository(config)
    try:
        record = repo.get_upload(owner, claim_id, upload_id)
        stream, _, _ = repo.content(owner, claim_id, upload_id, str(uuid4()))
        with stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        return record["estado"], digest
    finally:
        repo.engine.dispose()


def test_confirmed_receipt_and_original_bytes_survive_new_process(runtime: dict) -> None:
    data = image_bytes()
    _, url, upload = start_upload(runtime, data)
    assert put(runtime, url, data)[0] == 200
    user = runtime["users"][0]
    assert call(url + "/confirmacion", "POST", token=user["token"])[0] == 200
    with ProcessPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            _process_read_confirmed,
            (runtime["config"], user["id"], user["claim"]["id"], upload["id"]),
        )
        assert future.result() == ("confirmada", hashlib.sha256(data).hexdigest())


def test_geopdf_page_dictionary_requires_consent(runtime: dict) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    page[NameObject("/LGIDict")] = DictionaryObject()
    output = io.BytesIO()
    writer.write(output)
    data = output.getvalue()
    _, url, upload = start_upload(runtime, data, "application/pdf")
    assert put(runtime, url, data)[0] == 200
    status, problem = call(url + "/confirmacion", "POST", token=runtime["users"][0]["token"])
    assert (status, problem["code"]) == (422, "ubicacion_no_autorizada")
    assert not (runtime["config"].storage_root / upload["id"]).exists()


def test_pdf_attachment_is_not_an_unchecked_nested_upload(runtime: dict) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_attachment("nested.bin", b"unvalidated nested bytes")
    output = io.BytesIO()
    writer.write(output)
    data = output.getvalue()
    _, url, _ = start_upload(runtime, data, "application/pdf")
    assert put(runtime, url, data)[0] == 200
    status, problem = call(url + "/confirmacion", "POST", token=runtime["users"][0]["token"])
    assert (status, problem["code"]) == (422, "formato_invalido")


@pytest.mark.parametrize("confirmed", [False, True])
def test_operation_recovery_precedes_changed_mime_policy(runtime: dict, confirmed: bool) -> None:
    data = image_bytes()
    _, url, upload = start_upload(runtime, data)
    user = runtime["users"][0]
    repo = runtime["app"].extensions["siniestros_repository"]
    with repo.sessions() as session:
        operation = session.get(Upload, upload["id"]).operation
    if confirmed:
        assert put(runtime, url, data)[0] == 200
        assert call(url + "/confirmacion", "POST", token=user["token"])[0] == 200
    expected = call(url, token=user["token"])[1]
    app = create_app(replace(runtime["config"], allowed_mimes=("application/pdf",)))
    client = app.test_client()
    headers = {
        "Authorization": f"Bearer {runtime['internal']}",
        "X-Cliente-ID": user["id"],
        "Idempotency-Key": operation,
    }
    route = f"/v1/siniestros/{user['claim']['id']}/cargas"
    payload = {
        "mimeType": "image/jpeg",
        "tamanoBytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    try:
        replay = client.post(route, json=payload, headers=headers)
        assert replay.status_code == 200
        assert replay.get_json() == expected
        collision = client.post(route, json={**payload, "sha256": "0" * 64}, headers=headers)
        assert collision.status_code == 409
        assert collision.get_json()["code"] == "idempotencia_conflictiva"
        fresh = client.post(
            route, json=payload, headers={**headers, "Idempotency-Key": str(uuid4())}
        )
        assert fresh.status_code == 415
        assert fresh.get_json()["code"] == "formato_no_permitido"
        if confirmed:
            assert (
                client.post(
                    route + "/" + upload["id"] + "/confirmacion", headers=headers
                ).get_json()
                == expected
            )
        else:
            assert (
                client.put(
                    route + "/" + upload["id"] + "/contenido",
                    data=data,
                    headers={
                        **headers,
                        "Content-Type": "application/octet-stream",
                        "Upload-Offset": "0",
                    },
                ).status_code
                == 415
            )
        observed = call(url, token=user["token"])[1]
        if not confirmed:
            assert observed["ultimoResultado"]["code"] == "formato_no_permitido"
            assert observed["ultimoResultado"]["httpStatus"] == 415
            expected = {**expected, "ultimoResultado": observed["ultimoResultado"]}
        assert observed == expected
    finally:
        app.extensions["siniestros_repository"].engine.dispose()


def test_partial_queue_preserves_accepted_and_unprocessed_operations(runtime: dict) -> None:
    data = image_bytes()
    root, accepted_url, accepted = start_upload(runtime, data)
    _, interrupted_url, interrupted = start_upload(runtime, data)
    _, untouched_url, untouched = start_upload(runtime, data)
    user = runtime["users"][0]
    assert put(runtime, accepted_url, data)[0] == 200
    receipt = call(accepted_url + "/confirmacion", "POST", token=user["token"])
    assert receipt[0] == 200 and receipt[1]["estado"] == "confirmada"
    split = len(data) // 2
    assert put(runtime, interrupted_url, data[:split])[0] == 200
    assert (
        call(interrupted_url + "/confirmacion", "POST", token=user["token"])[1]["code"]
        == "carga_incompleta"
    )

    restarted = create_app(runtime["config"])
    headers = {"Authorization": f"Bearer {runtime['internal']}", "X-Cliente-ID": user["id"]}
    route = f"/v1/siniestros/{user['claim']['id']}"
    client = restarted.test_client()
    try:
        assert (
            client.get(route + "/cargas/" + accepted["id"], headers=headers).get_json()
            == (receipt[1])
        )
        partial = client.get(route + "/cargas/" + interrupted["id"], headers=headers).get_json()
        assert (partial["estado"], partial["offset"], partial["evidenciaId"]) == (
            "pendiente",
            split,
            None,
        )
        assert (
            client.get(route + "/cargas/" + untouched["id"], headers=headers).get_json()
            == untouched
        )
        assert call(untouched_url, token=user["token"])[1] == untouched
        assert call(accepted_url + "/confirmacion", "POST", token=user["token"]) == receipt
        assert put(runtime, interrupted_url, data[:split])[1]["code"] == "offset_conflictivo"
        assert put(runtime, interrupted_url, data[split:], split)[0] == 200
        assert call(interrupted_url + "/confirmacion", "POST", token=user["token"])[0] == 200
        ids = [
            item["id"] for item in call(root + "/evidencias", token=user["token"])[1]["evidencias"]
        ]
        assert ids.count(accepted["id"]) == ids.count(interrupted["id"]) == 1
        assert untouched["id"] not in ids
    finally:
        restarted.extensions["siniestros_repository"].engine.dispose()


def test_lost_registration_ack_replay_preserves_central_status_and_history(runtime: dict) -> None:
    user = runtime["users"][0]
    root = runtime["mobile"] + "/siniestros"
    operation = str(uuid4())
    payload = user["claimPayload"]
    headers = {"Idempotency-Key": operation}
    status, receipt = call(root, "POST", payload, user["token"], headers)
    assert status == 201
    repo = runtime["app"].extensions["siniestros_repository"]
    with repo.write() as session:
        claim = session.get(Claim, receipt["id"])
        claim.estado = "en_evaluacion"
        claim.actualizado_en = datetime.fromisoformat(receipt["registradoEn"]) + timedelta(
            seconds=1
        )
        session.add(
            CambioEstadoModel(
                siniestro_id=claim.id,
                secuencia=2,
                estado=claim.estado,
                ocurrido_en=claim.actualizado_en,
                visible_cliente=True,
            )
        )
    central = call(root + "/" + receipt["id"] + "/estado", token=user["token"])
    assert central[0] == 200 and central[1]["estado"] == "en_evaluacion"
    restarted = create_app(runtime["config"])
    client = restarted.test_client()
    internal_headers = {
        **headers,
        "Authorization": f"Bearer {runtime['internal']}",
        "X-Cliente-ID": user["id"],
    }
    try:
        replay = client.post("/v1/siniestros", json=payload, headers=internal_headers)
        assert replay.status_code == 200 and replay.get_json() == receipt
        collision = client.post(
            "/v1/siniestros", json={**payload, "lugar": "Otro lugar"}, headers=internal_headers
        )
        assert collision.status_code == 409
        assert collision.get_json()["code"] == "idempotencia_conflictiva"
        assert call(root + "/" + receipt["id"] + "/estado", token=user["token"]) == central
        with repo.sessions() as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(CambioEstadoModel)
                    .where(CambioEstadoModel.siniestro_id == receipt["id"])
                )
                == 2
            )
    finally:
        restarted.extensions["siniestros_repository"].engine.dispose()
