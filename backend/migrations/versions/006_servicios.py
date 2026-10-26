"""Servicios que ofrece cada técnico (HU-08)."""
from alembic import op
import sqlalchemy as sa

revision = "006_servicios"
down_revision = "005_especialidades_adicionales"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("servicios",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("perfiles_tecnicos.usuario_id"), nullable=False, index=True),
        sa.Column("especialidad_id", sa.Uuid(), sa.ForeignKey("especialidades.id"), nullable=False, index=True),
        sa.Column("nombre", sa.String(100), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    # FastAPI is the sole business-data entry point. No public Data API access.
    op.execute(sa.text('ALTER TABLE "servicios" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text('REVOKE ALL ON TABLE "servicios" FROM anon, authenticated'))


def downgrade():
    op.drop_table("servicios")