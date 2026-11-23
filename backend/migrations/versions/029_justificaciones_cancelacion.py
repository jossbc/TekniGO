"""HU-36 ST-04: justificaciones de cancelación."""
import sqlalchemy as sa
from alembic import op

revision = "029_justificaciones"
down_revision = "028_ajustes_servicio"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "justificaciones_cancelacion",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("cancelacion_id", sa.Integer(), sa.ForeignKey("cancelaciones_solicitud.id"), nullable=False),
        sa.Column("autor_uid", sa.String(128), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("evidencias", sa.JSON(), nullable=False),
        sa.Column("estado", sa.String(12), nullable=False, server_default="PENDIENTE"),
        sa.Column("enviada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resuelta_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("admin_uid", sa.String(128), nullable=True),
        sa.Column("observaciones_admin", sa.Text(), nullable=True),
        sa.UniqueConstraint("cancelacion_id", name="uq_justificacion_cancelacion"),
    )


def downgrade():
    op.drop_table("justificaciones_cancelacion")