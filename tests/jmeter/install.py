"""Install the checksum-pinned official Apache JMeter distribution."""

import argparse
import hashlib
import urllib.request
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

VERSION = "5.6.3"
SHA512 = (
    "387fadca903ee0aa30e3f2115fdfedb3898b102e6b9fe7cc3942703094bd2e65"
    "b235df2b0c6d0d3248e74c9a7950a36e42625fd74425368342c12e40b0163076"
)


def install(destination: Path) -> Path:
    home = destination / f"apache-jmeter-{VERSION}"
    if home.exists():
        raise FileExistsError(f"Refusing to overwrite an existing distribution: {home}")
    url = f"https://downloads.apache.org/jmeter/binaries/apache-jmeter-{VERSION}.zip"
    with TemporaryDirectory(prefix="jmeter-download-") as temporary:
        archive = Path(temporary) / "jmeter.zip"
        with urllib.request.urlopen(url, timeout=120) as response, archive.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        if hashlib.sha512(archive.read_bytes()).hexdigest() != SHA512:
            raise ValueError("Apache JMeter SHA-512 mismatch; refusing to extract.")
        with zipfile.ZipFile(archive) as bundle:
            for name in bundle.namelist():
                resolved = (destination / name).resolve()
                if not resolved.is_relative_to(home.resolve()):
                    raise ValueError("Unexpected archive path.")
            bundle.extractall(destination)
    return home


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=Path("reports/tools"))
    print(install(parser.parse_args().destination))


if __name__ == "__main__":
    main()
