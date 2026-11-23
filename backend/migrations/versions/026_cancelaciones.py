"""HU-36 ST-01: auditoría de cancelaciones."""
import sqlalchemy as sa
from alembic import op

revision = "026_cancelaciones"
down_revision = "025_tickets_incidencias"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "cancelaciones_solicitud",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("modalidad", sa.String(10), nullable=False),
        sa.Column("solicitud_id", sa.String(36), nullable=False),
        sa.Column("autor_uid", sa.String(128), nullable=False),
        sa.Column("autor_rol", sa.String(20), nullable=False),
        sa.Column("estado_previo", sa.String(30), nullable=False),
        sa.Column("causa", sa.String(40), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("evidencias", sa.JSON(), nullable=False),
        sa.Column("regla_aplicada", sa.String(60), nullable=False),
        sa.Column("genera_incumplimiento", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("responsable_rol", sa.String(20), nullable=True),
        sa.Column("responsable_uid", sa.String(128), nullable=True),
        sa.Column("estado_incumplimiento", sa.String(30), nullable=False, server_default="NO_APLICA"),
        sa.Column("permite_reapertura", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("cancelada_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("modalidad", "solicitud_id", name="uq_cancelacion_solicitud"),
    )
    op.create_index("ix_cancelaciones_responsable", "cancelaciones_solicitud",
                    ["responsable_uid", "estado_incumplimiento"])


def downgrade():
    op.drop_index("ix_cancelaciones_responsable", table_name="cancelaciones_solicitud")
    op.drop_table("cancelaciones_solicitud")