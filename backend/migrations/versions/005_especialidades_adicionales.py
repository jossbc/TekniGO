"""Especialidades adicionales y sugerencias de especialidades."""
import uuid
from alembic import op
import sqlalchemy as sa

revision = "005_especialidades_adicionales"
down_revision = "004_tecnicos_especialidades"
branch_labels = None
depends_on = None

NUEVAS = ["Albañilería", "Aire acondicionado y refrigeración", "Soldadura"]


def upgrade():
    especialidades = sa.table("especialidades", sa.column("id", sa.Uuid()), sa.column("nombre", sa.String()))
    op.bulk_insert(especialidades, [{"id": uuid.uuid4(), "nombre": n} for n in NUEVAS])

    op.create_table("sugerencias_especialidades",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("perfiles_tecnicos.usuario_id"), nullable=False, index=True),
        sa.Column("nombre", sa.String(80), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False, server_default="PENDIENTE"),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.execute(sa.text('ALTER TABLE "sugerencias_especialidades" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text('REVOKE ALL ON TABLE "sugerencias_especialidades" FROM anon, authenticated'))


def downgrade():
    op.drop_table("sugerencias_especialidades")
    for nombre in NUEVAS:
        op.execute(sa.text("DELETE FROM especialidades WHERE nombre = :n").bindparams(n=nombre))