"""Zonas de cobertura de los técnicos (HU-10)."""
from alembic import op
import sqlalchemy as sa

revision = "008_zonas_cobertura"
down_revision = "007_documentos_identidad"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "zonas_cobertura",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("perfiles_tecnicos.usuario_id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("departamento",sa.String(50),nullable=False),
        sa.Column("municipio", sa.String(50), nullable=False),
        sa.Column("sector", sa.String(100), nullable=False),
        sa.UniqueConstraint("usuario_id", "departamento" ,"municipio", "sector", name="uq_usuario,depto_municipio_sector"),
    )
    op.execute(sa.text('ALTER TABLE "zonas_cobertura" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text('REVOKE ALL ON TABLE "zonas_cobertura" FROM anon, authenticated'))


def downgrade():
    op.drop_table("zonas_cobertura")