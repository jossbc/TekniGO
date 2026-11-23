"""HU-42: liquidaciones de comisiones, contador de efectivo y auditoria administrativa."""
import sqlalchemy as sa
from alembic import op

revision = "031_liquidaciones_comisiones"
down_revision = "030_finanzas_reembolsos"
branch_labels = None
depends_on = None

_ACTIVE = sa.text("estado <> 'RECHAZADA'")


def upgrade():
    op.add_column(
        "perfiles_tecnicos",
        sa.Column("servicios_efectivo_sin_liquidar", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "liquidaciones_comisiones",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tecnico_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("importe_declarado", sa.Numeric(10, 2), nullable=False),
        sa.Column("referencia", sa.String(length=64), nullable=False),
        sa.Column("referencia_normalizada", sa.String(length=64), nullable=False),
        sa.Column("ruta_privada", sa.String(length=300), nullable=False),
        sa.Column("content_type", sa.String(length=50), nullable=False),
        sa.Column("tamano_bytes", sa.Integer(), nullable=False),
        sa.Column("comprobante_sha256", sa.String(length=64), nullable=False),
        sa.Column("estado", sa.String(length=20), nullable=False, server_default="PENDIENTE"),
        sa.Column("importe_verificado", sa.Numeric(10, 2), nullable=True),
        sa.Column("importe_aplicado", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("importe_excedente", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.Column("revisado_por", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=True),
        sa.Column("revisado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "estado IN ('PENDIENTE', 'APROBADA', 'RECHAZADA', 'EN_CONCILIACION')",
            name="ck_liquidacion_estado",
        ),
        sa.CheckConstraint("importe_declarado > 0", name="ck_liquidacion_importe_declarado"),
        sa.CheckConstraint(
            "importe_verificado IS NULL OR importe_verificado > 0",
            name="ck_liquidacion_importe_verificado",
        ),
        sa.CheckConstraint(
            "importe_aplicado >= 0 AND importe_excedente >= 0",
            name="ck_liquidacion_importes_no_negativos",
        ),
        sa.CheckConstraint(
            "estado = 'PENDIENTE' OR (revisado_por IS NOT NULL AND revisado_en IS NOT NULL)",
            name="ck_liquidacion_revision_responsable",
        ),
        sa.CheckConstraint(
            "estado NOT IN ('APROBADA', 'EN_CONCILIACION') OR importe_verificado IS NOT NULL",
            name="ck_liquidacion_importe_verificado_requerido",
        ),
        sa.CheckConstraint(
            "estado <> 'RECHAZADA' OR (observacion IS NOT NULL AND length(trim(observacion)) > 0)",
            name="ck_liquidacion_rechazo_observacion",
        ),
        sa.CheckConstraint("tamano_bytes > 0", name="ck_liquidacion_comprobante_tamano"),
    )
    op.create_index(
        "uq_liquidaciones_referencia_activa", "liquidaciones_comisiones", ["referencia_normalizada"],
        unique=True, postgresql_where=_ACTIVE, sqlite_where=_ACTIVE,
    )
    op.create_index(
        "uq_liquidaciones_comprobante_activo", "liquidaciones_comisiones", ["comprobante_sha256"],
        unique=True, postgresql_where=_ACTIVE, sqlite_where=_ACTIVE,
    )
    op.create_index("ix_liquidaciones_tecnico_estado", "liquidaciones_comisiones", ["tecnico_id", "estado"])

    op.create_table(
        "auditoria_liquidaciones_comisiones",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("liquidacion_id", sa.Uuid(), sa.ForeignKey("liquidaciones_comisiones.id"), nullable=False),
        sa.Column("tecnico_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("accion", sa.String(length=30), nullable=False),
        sa.Column("estado_anterior", sa.String(length=20), nullable=False),
        sa.Column("estado_nuevo", sa.String(length=20), nullable=False),
        sa.Column("referencia", sa.String(length=64), nullable=False),
        sa.Column("importe_verificado", sa.Numeric(10, 2), nullable=True),
        sa.Column("importe_aplicado", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("importe_excedente", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("deuda_anterior", sa.Numeric(10, 2), nullable=False),
        sa.Column("deuda_posterior", sa.Numeric(10, 2), nullable=False),
        sa.Column("contador_anterior", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("contador_posterior", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("restriccion_anterior", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("restriccion_posterior", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.Column("responsable_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "accion IN ('APROBACION_TOTAL', 'APROBACION_PARCIAL', 'SOBREPAGO_CONCILIACION', 'RECHAZO')",
            name="ck_auditoria_liquidacion_accion",
        ),
        sa.CheckConstraint(
            "deuda_anterior >= 0 AND deuda_posterior >= 0 AND importe_aplicado >= 0 AND importe_excedente >= 0",
            name="ck_auditoria_liquidacion_importes",
        ),
        sa.CheckConstraint(
            "contador_anterior >= 0 AND contador_posterior >= 0",
            name="ck_auditoria_liquidacion_contadores",
        ),
    )
    op.create_index(
        "ix_auditoria_liquidaciones_liquidacion",
        "auditoria_liquidaciones_comisiones",
        ["liquidacion_id"],
    )
    op.create_index(
        "ix_auditoria_liquidaciones_tecnico",
        "auditoria_liquidaciones_comisiones",
        ["tecnico_id"],
    )


def downgrade():
    op.drop_index("ix_auditoria_liquidaciones_tecnico", table_name="auditoria_liquidaciones_comisiones")
    op.drop_index("ix_auditoria_liquidaciones_liquidacion", table_name="auditoria_liquidaciones_comisiones")
    op.drop_table("auditoria_liquidaciones_comisiones")
    op.drop_index("ix_liquidaciones_tecnico_estado", table_name="liquidaciones_comisiones")
    op.drop_index("uq_liquidaciones_comprobante_activo", table_name="liquidaciones_comisiones")
    op.drop_index("uq_liquidaciones_referencia_activa", table_name="liquidaciones_comisiones")
    op.drop_table("liquidaciones_comisiones")
    op.drop_column("perfiles_tecnicos", "servicios_efectivo_sin_liquidar")
