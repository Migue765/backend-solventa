import re
import warnings
from pathlib import Path
from typing import Any

import av
from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from pypdf.generic import DictionaryObject, StreamObject

from siniestros.domain import Config, Problem

GEO = re.compile(rb"gps|latitude|longitude|location|geotag|iso6709|geospatial", re.I)


def _geo(value: Any, consent: bool) -> None:
    if not consent and GEO.search(str(value).encode("utf-8", errors="replace")):
        raise Problem(422, "ubicacion_no_autorizada")


def validate(path: Path, mime: str, consent: bool, config: Config) -> None:
    try:
        if mime.startswith("image/"):
            _image(path, mime, consent)
        elif mime == "application/pdf":
            _pdf(path, consent)
        else:
            _video(path, consent, config.video_seconds)
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        PdfReadError,
        av.error.FFmpegError,
        ValueError,
        EOFError,
        OSError,
    ) as error:
        raise Problem(422, "formato_invalido") from error


def _image(path: Path, mime: str, consent: bool) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as image:
            if image.format != {"image/jpeg": "JPEG", "image/png": "PNG"}[mime]:
                raise Problem(422, "formato_invalido")
            if image.width * image.height > 40_000_000 or getattr(image, "n_frames", 1) != 1:
                raise Problem(422, "formato_invalido")
            if not consent and image.getexif().get(34853) is not None:
                raise Problem(422, "ubicacion_no_autorizada")
            if not consent and "photoshop" in image.info:
                raise Problem(422, "ubicacion_no_autorizada")
            _geo(image.info, consent)
            _geo(dict(image.getexif()), consent)
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()


def _pdf(path: Path, consent: bool) -> None:
    with path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise Problem(422, "formato_invalido")
        stream.seek(0)
        reader = PdfReader(stream, strict=True)
        if reader.is_encrypted or not reader.pages or len(reader.pages) > 500:
            raise Problem(422, "formato_invalido")
        _geo(reader.metadata, consent)
        root = reader.trailer["/Root"]
        if not isinstance(root, DictionaryObject):
            raise Problem(422, "formato_invalido")
        names = root.get("/Names")
        if names is not None:
            names = names.get_object()
        if "/AF" in root or (isinstance(names, DictionaryObject) and "/EmbeddedFiles" in names):
            raise Problem(422, "formato_invalido")
        if "/Metadata" in root:
            metadata = root["/Metadata"]
            if not isinstance(metadata, StreamObject):
                raise Problem(422, "formato_invalido")
            _geo(metadata.get_data(), consent)
        for page in reader.pages:
            if "/AF" in page:
                raise Problem(422, "formato_invalido")
            if not consent and any(key in page for key in ("/VP", "/Measure", "/LGIDict")):
                raise Problem(422, "ubicacion_no_autorizada")
            if "/Metadata" in page:
                metadata = page["/Metadata"]
                if not isinstance(metadata, StreamObject):
                    raise Problem(422, "formato_invalido")
                _geo(metadata.get_data(), consent)


def _video(path: Path, consent: bool, max_seconds: int) -> None:
    with path.open("rb") as stream:
        header = stream.read(12)
    if len(header) != 12 or header[4:8] != b"ftyp":
        raise Problem(422, "formato_invalido")
    with av.open(str(path)) as container:
        _geo(container.metadata, consent)
        if len(container.streams.video) != 1:
            raise Problem(422, "formato_invalido")
        for track in container.streams:
            _geo(track.metadata, consent)
            if track.type not in {"video", "audio"}:
                raise Problem(422, "formato_invalido")
        video = container.streams.video[0]
        if video.width * video.height > 3840 * 2160:
            raise Problem(422, "video_fuera_limite")
        if container.duration is None or not 0 < container.duration <= max_seconds * av.time_base:
            raise Problem(422, "video_fuera_limite")
        frames = 0
        for frame in container.decode(video):
            frames += 1
            if frames > max_seconds * 60 or (frame.time is not None and frame.time > max_seconds):
                raise Problem(422, "video_fuera_limite")
        if not frames or (video.frames and frames != video.frames):
            raise Problem(422, "formato_invalido")
