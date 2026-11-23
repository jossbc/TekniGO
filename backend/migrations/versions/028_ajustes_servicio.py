"""HU-36 ST-03: ajuste único de presupuesto."""
import sqlalchemy as sa
from alembic import op

revision = "028_ajustes_servicio"
down_revision = "027_reapertura_licitacion"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ajustes_servicio",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("modalidad", sa.String(10), nullable=False),
        sa.Column("solicitud_id", sa.String(36), nullable=False),
        sa.Column("tecnico_uid", sa.String(128), nullable=False),
        sa.Column("cliente_uid", sa.String(128), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("monto_base", sa.Numeric(10, 2), nullable=False),
        sa.Column("nuevo_total", sa.Numeric(10, 2), nullable=False),
        sa.Column("diferencia", sa.Numeric(10, 2), nullable=False),
        sa.Column("estado", sa.String(12), nullable=False, server_default="PENDIENTE"),
        sa.Column("solicitado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("respondido_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("respuesta_uid", sa.String(128), nullable=True),
        sa.Column("comentario_respuesta", sa.Text(), nullable=True),
        sa.UniqueConstraint("modalidad", "solicitud_id", name="uq_ajuste_unico_solicitud"),
        sa.CheckConstraint("monto_base > 0", name="ck_ajuste_monto_base_positivo"),
        sa.CheckConstraint("nuevo_total > 0", name="ck_ajuste_nuevo_total_positivo"),
        sa.CheckConstraint("estado IN ('PENDIENTE', 'APROBADO', 'RECHAZADO')", name="ck_ajuste_estado"),
    )


def downgrade():
    op.drop_table("ajustes_servicio")