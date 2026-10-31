"""Agregar tarifa_base y unidad_cobro al modelo de servicios (HU-09 ST-01)."""
from alembic import op
import sqlalchemy as sa

revision = "009_tarifas_servicios"
down_revision = "008_zonas_cobertura"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("servicios", sa.Column("tarifa_base", sa.Numeric(precision=10, scale=2), nullable=False, server_default="0.0"))
    op.add_column("servicios", sa.Column("unidad_cobro", sa.String(length=20), nullable=False, server_default="POR_HORA"))


def downgrade():
    op.drop_column("servicios", "unidad_cobro")
    op.drop_column("servicios", "tarifa_base")