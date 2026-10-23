"""Disposable local application stack; never connects to configured remote databases."""

import argparse
import logging
import os
import secrets
import signal
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from types import TracebackType

from alembic import command
from alembic.config import Config as MigrationConfig
from bff_movil.app import create_app as mobile_app
from bff_movil.config import Config as MobileConfig
from bff_web.app import create_app as web_app
from bff_web.config import Config as WebConfig
from cotizacion.app import create_app as quote_app
from cotizacion.config import Config as QuoteConfig
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from flask import Flask
from identidad.app import create_app as identity_app
from identidad.config import Config as IdentityConfig
from perfilamiento.app import Config as ProfileConfig
from perfilamiento.app import create_app as profile_app
from perfilamiento.persistence import RepositorioPerfiles
from polizas.app import create_app as policy_app
from werkzeug.serving import BaseWSGIServer, WSGIRequestHandler, make_server

ROOT = Path(__file__).resolve().parents[2]


class QuietHandler(WSGIRequestHandler):
    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        # Request paths can contain personal identifiers; JTL contains safe labels instead.
        pass


class LocalStack:
    def __init__(self, directory: Path, web_port: int = 0, mobile_port: int = 0) -> None:
        self.directory = directory
        self.ports = {"web": web_port, "mobile": mobile_port}
        self.servers: dict[str, BaseWSGIServer] = {}
        self.threads: dict[str, threading.Thread] = {}
        self.apps: dict[str, Flask] = {}
        self.urls: dict[str, str] = {}

    def start(self, name: str, app: Flask, port: int = 0) -> str:
        server = make_server("127.0.0.1", port, app, threaded=True, request_handler=QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.apps[name], self.servers[name], self.threads[name] = app, server, thread
        self.urls[name] = f"http://127.0.0.1:{server.server_port}"
        thread.start()
        return self.urls[name]

    def stop(self, name: str) -> int:
        server = self.servers.pop(name)
        port = server.server_port
        server.shutdown()
        server.server_close()
        self.threads.pop(name).join(timeout=5)
        return port

    def restart_policy(self) -> None:
        port = self.stop("policies")
        self.resume_policy(port)

    def resume_policy(self, port: int) -> None:
        old = self.apps["policies"]
        old.extensions["polizas_engine"].dispose()
        self.start("policies", policy_app(self.databases["polizas"], self.internal_token), port)

    def __enter__(self) -> "LocalStack":
        # Alembic env.py reads these even when the URL is explicitly supplied.
        forbidden = ("DATABASE_URL", "DB_SCHEMA", "POLIZAS_DATABASE_URL")
        if any(os.environ.get(name) for name in forbidden):
            raise RuntimeError(
                "Unset DATABASE_URL, DB_SCHEMA and POLIZAS_DATABASE_URL for isolation."
            )
        self.directory.mkdir(parents=True, exist_ok=True)
        self.databases = {
            name: f"sqlite:///{(self.directory / (name + '.db')).as_posix()}"
            for name in ("identidad", "cotizacion", "polizas", "perfilamiento")
        }
        for name in ("identidad", "cotizacion", "polizas"):
            config = MigrationConfig(str(ROOT / "services" / name / "alembic.ini"))
            config.set_main_option("sqlalchemy.url", self.databases[name])
            command.upgrade(config, "head")
        profiles = RepositorioPerfiles(self.databases["perfilamiento"])
        profiles.inicializar()
        profiles.engine.dispose()
        self.internal_token = secrets.token_urlsafe(32)
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        issuer = "https://identity.local.solventa.test"
        try:
            identity = self.start(
                "identity",
                identity_app(
                    IdentityConfig(
                        database_url=self.databases["identidad"],
                        jwt_private_key=pem,
                        jwt_issuer=issuer,
                        internal_api_token=self.internal_token,
                    )
                ),
            )
            profile = self.start(
                "profile",
                profile_app(
                    ProfileConfig(
                        database_url=self.databases["perfilamiento"],
                        identidad_url=identity,
                        internal_api_token=self.internal_token,
                    )
                ),
            )
            policies = self.start(
                "policies", policy_app(self.databases["polizas"], self.internal_token)
            )
            quotes = self.start(
                "quotes",
                quote_app(
                    QuoteConfig(
                        database_url=self.databases["cotizacion"],
                        perfilamiento_habilitado=True,
                        perfilamiento_url=profile,
                        polizas_url=policies,
                        internal_api_token=self.internal_token,
                    )
                ),
            )
            self.start(
                "web",
                web_app(
                    WebConfig(
                        identity_url=identity,
                        jwt_issuer=issuer,
                        quote_service_url=quotes,
                        internal_api_token=self.internal_token,
                    )
                ),
                self.ports["web"],
            )
            self.start(
                "mobile",
                mobile_app(MobileConfig(identity_url=identity, jwt_issuer=issuer)),
                self.ports["mobile"],
            )
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        for name in list(reversed(self.servers)):
            self.stop(name)
        # SQLAlchemy engines are local only; dispose before Windows removes temporary DB files.
        from sqlalchemy import Engine

        for app in self.apps.values():
            for value in app.extensions.values():
                if isinstance(value, Engine):
                    value.dispose()
        if "identity" in self.apps:
            sessions = self.apps["identity"].extensions["consent_repository"]._sessions
            sessions.kw["bind"].dispose()
        if "quotes" in self.apps:
            sessions = self.apps["quotes"].extensions["repositorio_cotizaciones"]._sesiones
            sessions.kw["bind"].dispose()
        if "profile" in self.apps:
            self.apps["profile"].extensions["perfiles"].engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-port", type=int, default=5000)
    parser.add_argument("--mobile-port", type=int, default=5001)
    args = parser.parse_args()
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    stopped = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    with TemporaryDirectory(prefix="solventa-e2e-") as directory:
        with LocalStack(Path(directory), args.web_port, args.mobile_port) as stack:
            print(f"BFF web: {stack.urls['web']}; BFF movil: {stack.urls['mobile']}", flush=True)
            print(
                "SQLite temporal; JWT real; fuentes/pago/poliza academicos. Ctrl+C para cerrar.",
                flush=True,
            )
            stopped.wait()


if __name__ == "__main__":
    main()
