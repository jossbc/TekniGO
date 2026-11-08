"""Decisiones administrativas privadas por documento y versión de expediente."""
from alembic import op
import sqlalchemy as sa

revision = "014_decisiones_documentales"
down_revision = "013_vigencias_auditoria_tec"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "decisiones_documentales",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("documento_id", sa.Uuid(), sa.ForeignKey("documentos_identidad.id"), nullable=False),
        sa.Column("solicitud_id", sa.Uuid(), sa.ForeignKey("solicitudes_verificacion_identidad.id"), nullable=False),
        sa.Column("administrador_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("version_evaluada", sa.Integer(), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("observaciones", sa.Text(), nullable=True),
        sa.Column("legible", sa.Boolean(), nullable=False),
        sa.Column("identidad_coincide", sa.Boolean(), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_decisiones_documentales_documento_id", "decisiones_documentales", ["documento_id"])
    op.create_index("ix_decisiones_documentales_solicitud_id", "decisiones_documentales", ["solicitud_id"])
    op.execute('ALTER TABLE "decisiones_documentales" ENABLE ROW LEVEL SECURITY')
    op.execute('REVOKE ALL ON TABLE "decisiones_documentales" FROM anon, authenticated')


def downgrade():
    raise RuntimeError("La reversión requiere preservar las decisiones documentales privadas.")
