import math
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from asistencias.authorization import ResourceAuthorizer
from asistencias.domain import AssistanceInput, Config, Problem, ProviderQuery, fingerprint
from asistencias.models import Assistance, LocationAudit, Provider


def make_engine(url: str) -> Engine:
    engine = create_engine(url, pool_pre_ping=True)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def sqlite_settings(connection: Any, _: Any) -> None:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("PRAGMA synchronous=FULL")

    return engine


def distance_km(latitude: float, longitude: float, provider: Provider) -> float:
    radius = 6371.0088
    lat1, lat2 = math.radians(latitude), math.radians(float(provider.latitud))
    delta_lat = lat2 - lat1
    delta_lon = math.radians(float(provider.longitud) - longitude)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    )
    return radius * 2 * math.asin(math.sqrt(haversine))


class Repository:
    def __init__(self, config: Config, authorizer: ResourceAuthorizer) -> None:
        self.config = config
        self.authorizer = authorizer
        self.engine = make_engine(config.database_url)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def write(self) -> Iterator[Session]:
        with self.sessions() as session, session.begin():
            if self.engine.dialect.name == "sqlite":
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            elif self.engine.dialect.name == "postgresql":
                session.connection().exec_driver_sql("SELECT pg_advisory_xact_lock(207020)")
            else:
                raise ValueError("Asistencias solo admite SQLite y PostgreSQL.")
            yield session

    def audit_location(
        self, client_id: UUID, purpose: str, correlation_id: UUID, assistance_id: UUID | None = None
    ) -> None:
        with self.write() as session:
            session.add(
                LocationAudit(
                    id=str(uuid4()),
                    cliente_id=str(client_id),
                    proposito=purpose,
                    correlation_id=str(correlation_id),
                    asistencia_id=str(assistance_id) if assistance_id else None,
                    usada_en=datetime.now(UTC),
                )
            )

    def providers(
        self, client_id: UUID, query: ProviderQuery, correlation_id: UUID
    ) -> list[dict[str, object]]:
        query.ubicacion.validate_time(datetime.now(UTC))
        self.audit_location(client_id, "consulta_prestadores", correlation_id)
        with self.sessions() as session:
            providers = session.scalars(
                select(Provider).where(
                    Provider.disponible.is_(True),
                    *(
                        [Provider.tipo_servicio == query.tipoServicio]
                        if query.tipoServicio is not None
                        else []
                    ),
                )
            ).all()
        nearby = [
            (provider, distance_km(query.ubicacion.latitud, query.ubicacion.longitud, provider))
            for provider in providers
        ]
        return [
            {
                "id": provider.id,
                "nombre": provider.nombre,
                "tipoServicio": provider.tipo_servicio,
                "distanciaKm": round(distance, 1),
            }
            for provider, distance in sorted(nearby, key=lambda item: (item[1], item[0].id))
            if distance <= query.radioKm
        ]

    def register(
        self,
        client_id: UUID,
        operation_id: UUID,
        data: AssistanceInput,
        correlation_id: UUID,
    ) -> tuple[dict[str, object], int]:
        payload = data.model_dump(mode="json")
        digest = fingerprint(payload)
        existing = self._existing(client_id, operation_id)
        if existing is not None:
            if existing.fingerprint != digest:
                raise Problem(409, "idempotencia_conflictiva")
            return self._record(existing), 200

        data.ubicacion.validate_time(datetime.now(UTC))
        self.audit_location(client_id, "registro_asistencia", correlation_id)
        if data.polizaId is not None:
            authorized = self.authorizer.authorized_policy(client_id, data.polizaId, correlation_id)
        elif data.siniestroId is not None:
            authorized = self.authorizer.authorized_claim(
                client_id, data.siniestroId, correlation_id
            )
        else:
            raise Problem(400, "solicitud_invalida")
        if not authorized:
            raise Problem(404, "recurso_no_autorizado")

        with self.write() as session:
            existing = session.scalar(
                select(Assistance).where(
                    Assistance.cliente_id == str(client_id),
                    Assistance.operacion_id == str(operation_id),
                )
            )
            if existing is not None:
                if existing.fingerprint != digest:
                    raise Problem(409, "idempotencia_conflictiva")
                return self._record(existing), 200
            provider = session.get(Provider, str(data.prestadorId))
            if (
                provider is None
                or not provider.disponible
                or provider.tipo_servicio != data.tipoAsistencia
                or distance_km(data.ubicacion.latitud, data.ubicacion.longitud, provider)
                > self.config.max_provider_distance_km
            ):
                raise Problem(409, "prestador_no_disponible")
            assistance = Assistance(
                id=str(uuid4()),
                cliente_id=str(client_id),
                operacion_id=str(operation_id),
                fingerprint=digest,
                prestador_id=provider.id,
                tipo_asistencia=data.tipoAsistencia,
                poliza_id=str(data.polizaId) if data.polizaId else None,
                siniestro_id=str(data.siniestroId) if data.siniestroId else None,
                latitud=Decimal(str(data.ubicacion.latitud)),
                longitud=Decimal(str(data.ubicacion.longitud)),
                origen_ubicacion=data.ubicacion.origen,
                ubicacion_confirmada_en=data.ubicacion.confirmadaEn,
                estado="registrada",
                creada_en=datetime.now(UTC),
            )
            session.add(assistance)
            session.flush()
            return self._record(assistance), 201

    def _existing(self, client_id: UUID, operation_id: UUID) -> Assistance | None:
        with self.sessions() as session:
            return session.scalar(
                select(Assistance).where(
                    Assistance.cliente_id == str(client_id),
                    Assistance.operacion_id == str(operation_id),
                )
            )

    @staticmethod
    def _record(assistance: Assistance) -> dict[str, object]:
        created = assistance.creada_en
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return {
            "id": assistance.id,
            "estado": assistance.estado,
            "prestadorId": assistance.prestador_id,
            "tipoAsistencia": assistance.tipo_asistencia,
            "creadaEn": created.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        }
