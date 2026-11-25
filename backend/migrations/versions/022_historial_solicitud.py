"""Persist request lifecycle history."""
from alembic import op
import sqlalchemy as sa

revision = "022_historial_solicitud"
down_revision = "021_finalizacion_confirmacion"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "historial_estados_solicitud",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("modalidad", sa.String(10), nullable=False),
        sa.Column("solicitud_id", sa.String(36), nullable=False),
        sa.Column("estado", sa.String(30), nullable=False),
        sa.Column("fecha", sa.DateTime(timezone=True)),
        sa.Column("responsable", sa.String(20)),
        sa.Column("responsable_id", sa.String(128)),
    )
    op.create_index(
        "ix_historial_estado_solicitud",
        "historial_estados_solicitud",
        ["modalidad", "solicitud_id", "id"],
    )
    op.execute("ALTER TABLE historial_estados_solicitud ENABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON TABLE historial_estados_solicitud FROM anon, authenticated")


def downgrade():
    op.drop_table("historial_estados_solicitud")
