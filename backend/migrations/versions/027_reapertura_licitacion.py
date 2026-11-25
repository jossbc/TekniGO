"""HU-36 ST-02: enlace de reapertura entre solicitud cancelada y nueva licitación."""
import sqlalchemy as sa
from alembic import op

revision = "027_reapertura_licitacion"
down_revision = "026_cancelaciones"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("solicitudes_abiertas", sa.Column("solicitud_origen_id", sa.String(36), nullable=True))
    op.add_column("solicitudes_abiertas", sa.Column("solicitud_origen_modalidad", sa.String(10), nullable=True))
    op.create_index("ix_solicitudes_abiertas_solicitud_origen_id", "solicitudes_abiertas", ["solicitud_origen_id"])
    op.add_column("cancelaciones_solicitud", sa.Column("solicitud_reabierta_id", sa.String(36), nullable=True))
    op.add_column("cancelaciones_solicitud", sa.Column("reabierta_en", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_cancelaciones_reabierta", "cancelaciones_solicitud", ["solicitud_reabierta_id"])


def downgrade():
    op.drop_constraint("uq_cancelaciones_reabierta", "cancelaciones_solicitud", type_="unique")
    op.drop_column("cancelaciones_solicitud", "reabierta_en")
    op.drop_column("cancelaciones_solicitud", "solicitud_reabierta_id")
    op.drop_index("ix_solicitudes_abiertas_solicitud_origen_id", table_name="solicitudes_abiertas")
    op.drop_column("solicitudes_abiertas", "solicitud_origen_modalidad")
    op.drop_column("solicitudes_abiertas", "solicitud_origen_id")