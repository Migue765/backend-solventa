"""Fresh, loopback-only PostgreSQL cluster for synthetic web acceptance."""

import json
import os
import secrets
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import psycopg
from alembic import command
from alembic.config import Config
from perfilamiento.persistence import RepositorioPerfiles
from psycopg import sql
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[1]
DATABASE = "solventa_prod"
TABLE_COUNTS = {
    "identidad": 14,
    "cotizacion": 20,
    "perfilamiento": 11,
    "polizas": 7,
    "siniestros": 23,
    "notificaciones": 4,
    "asistencias": 3,
}


@dataclass
class LocalDatabase:
    runtime_urls: dict[str, str]
    migration_urls: dict[str, str]
    evidence: dict[str, object]


def _run(binary: Path, *args: str, log: Path) -> None:
    # pg_ctl's background Windows child can inherit pipes and prevent communicate() from ending.
    with log.open("ab") as output:
        result = subprocess.run(
            [str(binary), *args], stdout=output, stderr=subprocess.STDOUT, timeout=90, check=False
        )
    if result.returncode:
        raise RuntimeError(f"{binary.name} fallo; revisar el directorio privado del bootstrap.")


def _connect(url: str) -> psycopg.Connection[Any]:
    # SQLAlchemy's driver suffix is not part of a libpq connection URI.
    return psycopg.connect(url.replace("postgresql+psycopg:", "postgresql:", 1), autocommit=True)


def _migrate(urls: dict[str, str]) -> None:
    for domain, url in urls.items():
        if domain == "perfilamiento":
            repository = RepositorioPerfiles(url)
            try:
                repository.inicializar()
            finally:
                repository.engine.dispose()
        else:
            config = Config(str(ROOT / "services" / domain / "alembic.ini"))
            config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
            command.upgrade(config, "head")
            history = "alembic_version_identidad" if domain == "identidad" else "alembic_version"
            with _connect(url) as owner:
                owner.execute(
                    sql.SQL("REVOKE ALL ON TABLE {} FROM {}").format(
                        sql.Identifier(history), sql.Identifier(f"{domain}_runtime")
                    )
                )


def _denied(connection: psycopg.Connection[Any], statement: sql.SQL | sql.Composed) -> None:
    try:
        connection.execute(statement)
    except psycopg.errors.InsufficientPrivilege:
        return
    raise RuntimeError("La comprobacion PostgreSQL permitio acceso fuera del rol/esquema.")


def _verify(database: LocalDatabase) -> dict[str, object]:
    evidence: dict[str, object] = {}
    for url in database.migration_urls.values():
        with _connect(url) as owner:
            owner.execute("CREATE TABLE __bootstrap_probe (id integer PRIMARY KEY, value text)")
    try:
        for domain, expected in TABLE_COUNTS.items():
            with _connect(database.runtime_urls[domain]) as runtime:
                row = runtime.execute(
                    "SELECT current_database(), current_schema(), current_user, session_user"
                ).fetchone()
                if row != (DATABASE, domain, f"{domain}_runtime", f"{domain}_runtime"):
                    raise RuntimeError("Base/esquema/rol runtime inesperado.")
                tables = runtime.execute(
                    "SELECT tablename, tableowner FROM pg_tables WHERE schemaname = %s "
                    "AND tablename NOT LIKE 'alembic_version%%' "
                    "AND tablename <> '__bootstrap_probe'",
                    (domain,),
                ).fetchall()
                if len(tables) != expected:
                    raise RuntimeError(f"Conteo de tablas inesperado para {domain}: {len(tables)}.")
                if any(table[1] != f"{domain}_owner" for table in tables):
                    raise RuntimeError("Una tabla no pertenece al propietario de su dominio.")
                schema_owner = runtime.execute(
                    "SELECT n.nspowner::regrole::text, r.rolcanlogin "
                    "FROM pg_namespace n JOIN pg_roles r ON r.oid = n.nspowner "
                    "WHERE n.nspname = %s",
                    (domain,),
                ).fetchone()
                privileges = runtime.execute(
                    "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication "
                    "FROM pg_roles WHERE rolname = current_user"
                ).fetchone()
                search_path = runtime.execute("SHOW search_path").fetchone()
                if (
                    schema_owner != (f"{domain}_owner", False)
                    or privileges != (False, False, False, False)
                    or search_path != (domain,)
                ):
                    raise RuntimeError("Propiedad/search_path/privilegios runtime incorrectos.")
                runtime.execute("INSERT INTO __bootstrap_probe VALUES (1, 'synthetic')")
                runtime.execute("UPDATE __bootstrap_probe SET value = 'updated' WHERE id = 1")
                if runtime.execute("SELECT value FROM __bootstrap_probe").fetchone() != (
                    "updated",
                ):
                    raise RuntimeError("Fallo CRUD del esquema propietario.")
                runtime.execute("DELETE FROM __bootstrap_probe WHERE id = 1")
                _denied(runtime, sql.SQL("CREATE TABLE __forbidden_ddl (id integer)"))
                _denied(runtime, sql.SQL("CREATE TABLE public.__forbidden_ddl (id integer)"))
                _denied(runtime, sql.SQL("SET ROLE {}").format(sql.Identifier(f"{domain}_owner")))
                if domain != "perfilamiento":
                    history = (
                        "alembic_version_identidad" if domain == "identidad" else "alembic_version"
                    )
                    _denied(runtime, sql.SQL("SELECT * FROM {}").format(sql.Identifier(history)))
                for other in TABLE_COUNTS:
                    if other != domain:
                        _denied(
                            runtime,
                            sql.SQL("SELECT * FROM {}.__bootstrap_probe").format(
                                sql.Identifier(other)
                            ),
                        )
                        _denied(
                            runtime,
                            sql.SQL(
                                "INSERT INTO {}.__bootstrap_probe VALUES (2, 'forbidden')"
                            ).format(sql.Identifier(other)),
                        )
            with _connect(database.migration_urls[domain]) as migrator:
                role = migrator.execute(
                    "SELECT current_schema(), current_user, session_user"
                ).fetchone()
                if role != (domain, f"{domain}_owner", f"{domain}_migrator"):
                    raise RuntimeError("Rol migrador/propietario inesperado.")
                for other in TABLE_COUNTS:
                    if other != domain:
                        _denied(
                            migrator,
                            sql.SQL("SELECT * FROM {}.__bootstrap_probe").format(
                                sql.Identifier(other)
                            ),
                        )
                        _denied(
                            migrator,
                            sql.SQL("CREATE TABLE {}.__forbidden_ddl (id integer)").format(
                                sql.Identifier(other)
                            ),
                        )
                version_table = (
                    "alembic_version_identidad" if domain == "identidad" else "alembic_version"
                )
                revisions = (
                    migrator.execute(
                        sql.SQL("SELECT version_num FROM {}").format(sql.Identifier(version_table))
                    ).fetchall()
                    if domain != "perfilamiento"
                    else []
                )
            evidence[domain] = {
                "database": DATABASE,
                "schema": domain,
                "owner": f"{domain}_owner",
                "runtime": f"{domain}_runtime",
                "migrator": f"{domain}_migrator",
                "domainTables": expected,
                "revisions": [row[0] for row in revisions],
                "crud": True,
                "ownerNoLogin": True,
                "tableOwnersVerified": True,
                "runtimeNotPrivileged": True,
                "searchPath": domain,
                "runtimeDdlDenied": True,
                "crossDomainReadsDenied": 6,
                "crossDomainWritesDenied": 6,
                "migrationCrossDomainReadsDenied": 6,
                "migrationCrossDomainDdlDenied": 6,
                "migrationHistoryRuntimeDenied": domain != "perfilamiento",
            }
    finally:
        for url in database.migration_urls.values():
            with _connect(url) as owner:
                owner.execute("DROP TABLE __bootstrap_probe")
    return evidence


def _provision(admin_url: str, port: int, password: str) -> LocalDatabase:
    def url(user: str, secret: str, database: str, owner: str | None = None) -> str:
        return URL.create(
            "postgresql+psycopg",
            username=user,
            password=secret,
            host="127.0.0.1",
            port=port,
            database=database,
            query={"options": f"-crole={owner}"} if owner else {},
        ).render_as_string(hide_password=False)

    with _connect(admin_url) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DATABASE)))
        admin.execute("REVOKE CONNECT ON DATABASE postgres FROM PUBLIC")
        admin.execute("REVOKE CONNECT ON DATABASE template1 FROM PUBLIC")
        admin.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(DATABASE))
        )
    runtime_urls: dict[str, str] = {}
    migration_urls: dict[str, str] = {}
    with _connect(url("local_bootstrap", password, DATABASE)) as admin:
        admin.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        for domain in TABLE_COUNTS:
            owner, runtime, migrator = (
                f"{domain}_{suffix}" for suffix in ("owner", "runtime", "migrator")
            )
            admin.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(owner)))
            for role in (runtime, migrator):
                secret = secrets.token_urlsafe(32)
                admin.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(secret)
                    )
                )
                admin.execute(
                    sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                        sql.Identifier(DATABASE), sql.Identifier(role)
                    )
                )
                admin.execute(
                    sql.SQL("ALTER ROLE {} IN DATABASE {} SET search_path = {}").format(
                        sql.Identifier(role), sql.Identifier(DATABASE), sql.Identifier(domain)
                    )
                )
                if role == runtime:
                    runtime_urls[domain] = url(role, secret, DATABASE)
                else:
                    migration_urls[domain] = url(role, secret, DATABASE, owner)
            admin.execute(
                sql.SQL("GRANT {} TO {}").format(sql.Identifier(owner), sql.Identifier(migrator))
            )
            admin.execute(
                sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(
                    sql.Identifier(domain), sql.Identifier(owner)
                )
            )
            admin.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(domain), sql.Identifier(runtime)
                )
            )
            for objects, privileges in (
                ("TABLES", "SELECT, INSERT, UPDATE, DELETE"),
                ("SEQUENCES", "USAGE, SELECT"),
            ):
                admin.execute(
                    sql.SQL(
                        "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} GRANT {} ON {} TO {}"
                    ).format(
                        sql.Identifier(owner),
                        sql.Identifier(domain),
                        sql.SQL(privileges),
                        sql.SQL(objects),
                        sql.Identifier(runtime),
                    )
                )
    return LocalDatabase(runtime_urls, migration_urls, {})


@contextmanager
def postgres_cluster(binaries: Path, directory: Path, port: int) -> Iterator[LocalDatabase]:
    """Never attach to an existing cluster or expose a server beyond loopback."""
    data = directory / "postgres"
    data.mkdir(exist_ok=False)
    password = secrets.token_urlsafe(32)
    password_file = data / "admin-password"
    log = data / "native-commands.log"
    with os.fdopen(os.open(password_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w") as out:
        out.write(password)
    version = subprocess.check_output(
        [str(binaries / "initdb.exe"), "--version"], text=True, timeout=10
    )
    if not version.startswith("initdb (PostgreSQL) 17."):
        raise RuntimeError("Este bootstrap requiere PostgreSQL 17.")
    _run(
        binaries / "initdb.exe",
        "-D",
        str(data / "data"),
        "-U",
        "local_bootstrap",
        "--pwfile",
        str(password_file),
        "--auth",
        "scram-sha-256",
        "--encoding",
        "UTF8",
        "--locale",
        "C",
        log=log,
    )
    try:
        _run(
            binaries / "pg_ctl.exe",
            "-D",
            str(data / "data"),
            "-l",
            str(data / "postgres.log"),
            "-o",
            f"-h 127.0.0.1 -p {port} -c log_statement=none -c log_min_error_statement=panic",
            "-w",
            "-t",
            "30",
            "start",
            log=log,
        )
        admin_url = URL.create(
            "postgresql+psycopg",
            username="local_bootstrap",
            password=password,
            host="127.0.0.1",
            port=port,
            database="postgres",
        ).render_as_string(hide_password=False)
        database = _provision(admin_url, port, password)
        _migrate(database.migration_urls)
        _migrate(database.migration_urls)
        database.evidence = _verify(database)
        with os.fdopen(
            os.open(data / "connections.json", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w"
        ) as out:
            json.dump(
                {
                    "syntheticOnly": True,
                    "runtime": database.runtime_urls,
                    "migrations": database.migration_urls,
                },
                out,
                indent=2,
            )
        yield database
    finally:
        if (data / "data" / "postmaster.pid").exists():
            _run(
                binaries / "pg_ctl.exe",
                "-D",
                str(data / "data"),
                "-m",
                "fast",
                "-w",
                "stop",
                log=log,
            )
