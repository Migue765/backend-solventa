"""Crea prestadores, asistencias y auditoria de uso de ubicacion."""

import sqlalchemy as sa
from alembic import op

revision = "0001_asistencias"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prestadores",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("nombre", sa.String(120), nullable=False),
        sa.Column("tipo_servicio", sa.String(80), nullable=False),
        sa.Column("latitud", sa.Numeric(9, 6), nullable=False),
        sa.Column("longitud", sa.Numeric(9, 6), nullable=False),
        sa.Column("disponible", sa.Boolean(), nullable=False),
    )
    op.create_index(
        "ix_prestadores_tipo_disponible",
        "prestadores",
        ["tipo_servicio", "disponible"],
    )
    op.create_table(
        "solicitudes_asistencia",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("cliente_id", sa.String(36), nullable=False),
        sa.Column("operacion_id", sa.String(36), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("prestador_id", sa.String(36), sa.ForeignKey("prestadores.id"), nullable=False),
        sa.Column("tipo_asistencia", sa.String(80), nullable=False),
        sa.Column("poliza_id", sa.String(36), nullable=True),
        sa.Column("siniestro_id", sa.String(36), nullable=True),
        sa.Column("latitud", sa.Numeric(9, 6), nullable=False),
        sa.Column("longitud", sa.Numeric(9, 6), nullable=False),
        sa.Column("origen_ubicacion", sa.String(20), nullable=False),
        sa.Column("ubicacion_confirmada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("estado IN ('registrada')"),
        sa.CheckConstraint(
            "(poliza_id IS NOT NULL AND siniestro_id IS NULL) OR "
            "(poliza_id IS NULL AND siniestro_id IS NOT NULL)"
        ),
        sa.UniqueConstraint("cliente_id", "operacion_id"),
    )
    op.create_table(
        "auditoria_ubicacion",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("cliente_id", sa.String(36), nullable=False),
        sa.Column("proposito", sa.String(40), nullable=False),
        sa.Column("correlation_id", sa.String(36), nullable=False),
        sa.Column("asistencia_id", sa.String(36), nullable=True),
        sa.Column("usada_en", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "proposito IN ('consulta_prestadores', 'registro_asistencia')"
        ),
    )
    op.create_index(
        "ix_auditoria_ubicacion_cliente_fecha",
        "auditoria_ubicacion",
        ["cliente_id", "usada_en"],
    )
    op.bulk_insert(
        sa.table(
            "prestadores",
            sa.column("id", sa.String),
            sa.column("nombre", sa.String),
            sa.column("tipo_servicio", sa.String),
            sa.column("latitud", sa.Numeric),
            sa.column("longitud", sa.Numeric),
            sa.column("disponible", sa.Boolean),
        ),
        [
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "nombre": "Red Solventa Gruas Centro",
                "tipo_servicio": "grua",
                "latitud": 4.711000,
                "longitud": -74.072100,
                "disponible": True,
            },
            {
                "id": "22222222-2222-4222-8222-222222222222",
                "nombre": "Red Solventa Asistencia Vial",
                "tipo_servicio": "asistencia_vial",
                "latitud": 4.705000,
                "longitud": -74.060000,
                "disponible": True,
            },
            {
                "id": "33333333-3333-4333-8333-333333333333",
                "nombre": "Red Solventa Gruas Antioquia",
                "tipo_servicio": "grua",
                "latitud": 6.244200,
                "longitud": -75.581200,
                "disponible": True,
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_auditoria_ubicacion_cliente_fecha", table_name="auditoria_ubicacion")
    op.drop_table("auditoria_ubicacion")
    op.drop_table("solicitudes_asistencia")
    op.drop_index("ix_prestadores_tipo_disponible", table_name="prestadores")
    op.drop_table("prestadores")
