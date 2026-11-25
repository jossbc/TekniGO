"""HU-36 ST-05: reembolsos idempotentes y libro financiero."""
import sqlalchemy as sa
from alembic import op

revision = "030_finanzas_reembolsos"
down_revision = "029_justificaciones"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "reembolsos_solicitud",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("modalidad", sa.String(10), nullable=False),
        sa.Column("solicitud_id", sa.String(36), nullable=False),
        sa.Column("cliente_uid", sa.String(128), nullable=False),
        sa.Column("monto_base_reembolsado", sa.Numeric(10, 2), nullable=False, server_default="0.00"),
        sa.Column("monto_ajuste_reembolsado", sa.Numeric(10, 2), nullable=False, server_default="0.00"),
        sa.Column("monto_reembolso", sa.Numeric(10, 2), nullable=False),
        sa.Column("monto_comision", sa.Numeric(10, 2), nullable=False, server_default="0.00"),
        sa.Column("referencia_pasarela_origen", sa.String(220), nullable=False),
        sa.Column("referencia_reembolso_pasarela", sa.String(120), nullable=False),
        sa.Column("estado_pasarela", sa.String(30), nullable=False, server_default="EN_PROCESO_PASARELA"),
        sa.Column("iniciado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmado_en", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("modalidad", "solicitud_id", name="uq_reembolso_solicitud"),
        sa.CheckConstraint("monto_reembolso > 0", name="ck_reembolso_monto_positivo"),
        sa.CheckConstraint("monto_comision = 0", name="ck_reembolso_sin_comision"),
        sa.CheckConstraint(
            "estado_pasarela IN ('EN_PROCESO_PASARELA', 'CONFIRMADO_PASARELA')",
            name="ck_reembolso_estado_pasarela",
        ),
    )

    op.create_table(
        "movimientos_libro_financiero",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("clave_idempotencia", sa.String(120), nullable=False),
        sa.Column("modalidad", sa.String(10), nullable=False),
        sa.Column("solicitud_id", sa.String(36), nullable=False),
        sa.Column("tipo_movimiento", sa.String(40), nullable=False),
        sa.Column("moneda", sa.String(5), nullable=False, server_default="HNL"),
        sa.Column("monto_base", sa.Numeric(10, 2), nullable=False),
        sa.Column("monto_ajuste", sa.Numeric(10, 2), nullable=False, server_default="0.00"),
        sa.Column("monto_total", sa.Numeric(10, 2), nullable=False),
        sa.Column("tasa_comision", sa.Numeric(6, 4), nullable=False, server_default="0.0000"),
        sa.Column("monto_comision", sa.Numeric(10, 2), nullable=False, server_default="0.00"),
        sa.Column("metodo_pago", sa.String(30), nullable=False),
        sa.Column("referencia_pasarela", sa.String(220), nullable=True),
        sa.Column("detalle", sa.JSON(), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("clave_idempotencia", name="uq_libro_financiero_idempotencia"),
    )
    op.create_index(
        "ix_libro_financiero_solicitud",
        "movimientos_libro_financiero",
        ["modalidad", "solicitud_id"],
    )


def downgrade():
    op.drop_index("ix_libro_financiero_solicitud", table_name="movimientos_libro_financiero")
    op.drop_table("movimientos_libro_financiero")
    op.drop_table("reembolsos_solicitud")