"""Persist service reception, incidents and closing records."""
import sqlalchemy as sa
from alembic import op

revision = "024_recepcion_persistente"
down_revision = "023_configuracion_catalogo"
branch_labels = None
depends_on = None

TABLES = ("solicitudes_directas", "solicitudes_abiertas")


def upgrade():
    for table in TABLES:
        op.add_column(table, sa.Column("seguimiento_recepcion", sa.JSON(), nullable=True))


def downgrade():
    for table in TABLES:
        op.drop_column(table, "seguimiento_recepcion")
