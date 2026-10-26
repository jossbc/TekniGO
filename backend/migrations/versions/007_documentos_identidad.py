"""Documentos privados de identidad del usuario (HU-15)."""
from alembic import op
import sqlalchemy as sa

revision = "007_documentos_identidad"
down_revision = "006_servicios"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "documentos_identidad",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False, index=True),
        sa.Column("tipo", sa.String(30), nullable=False),
        sa.Column("ruta_privada", sa.String(500), nullable=False, unique=True),
        sa.Column("content_type", sa.String(50), nullable=False),
        sa.Column("tamano_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False, server_default="PENDIENTE"),
        sa.Column("observaciones", sa.Text(), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("tipo IN ('DNI_FRONTAL', 'DNI_REVERSO', 'SELFIE')", name="ck_documento_identidad_tipo"),
        sa.CheckConstraint("estado IN ('PENDIENTE', 'APROBADO', 'RECHAZADO')", name="ck_documento_identidad_estado"),
    )
    # FastAPI is the sole business-data entry point. No public Data API access.
    op.execute(sa.text('ALTER TABLE "documentos_identidad" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text('REVOKE ALL ON TABLE "documentos_identidad" FROM anon, authenticated'))


def downgrade():
    op.drop_table("documentos_identidad")

