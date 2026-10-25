"""Link technicians with their specialties for HU-07."""
from alembic import op
import sqlalchemy as sa

revision = "004_tecnicos_especialidades"
down_revision = "003_especialidades"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("tecnicos_especialidades",
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("perfiles_tecnicos.usuario_id"), primary_key=True),
        sa.Column("especialidad_id", sa.Uuid(), sa.ForeignKey("especialidades.id"), primary_key=True))
    # FastAPI is the sole business-data entry point. No public Data API access.
    op.execute(sa.text('ALTER TABLE "tecnicos_especialidades" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text('REVOKE ALL ON TABLE "tecnicos_especialidades" FROM anon, authenticated'))


def downgrade():
    op.drop_table("tecnicos_especialidades")