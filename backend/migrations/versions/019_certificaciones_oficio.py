"""Optional trade certificates and their administrative decision history."""
from alembic import op
import sqlalchemy as sa

revision = "019_certificaciones_oficio"
down_revision = "018_propuestas_licitacion"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "certificaciones_oficio",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("perfiles_tecnicos.usuario_id"), nullable=False),
        sa.Column("titulo", sa.String(150), nullable=False),
        sa.Column("ruta_privada", sa.String(500), nullable=False, unique=True),
        sa.Column("content_type", sa.String(50), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False, server_default="PENDIENTE"),
        sa.Column("observaciones", sa.Text()),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("estado IN ('PENDIENTE', 'APROBADO', 'RECHAZADO', 'REVOCADO')", name="ck_certificacion_estado"),
    )
    op.create_index("ix_certificacion_tecnico_estado", "certificaciones_oficio", ["usuario_id", "estado"])
    op.create_table(
        "decisiones_certificaciones_oficio",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("certificacion_id", sa.Uuid(), sa.ForeignKey("certificaciones_oficio.id"), nullable=False),
        sa.Column("administrador_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("estado", sa.String(20), nullable=False),
        sa.Column("observaciones", sa.Text()),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_decisiones_certificaciones_oficio_certificacion_id", "decisiones_certificaciones_oficio", ["certificacion_id"])

    for table in ("certificaciones_oficio", "decisiones_certificaciones_oficio"):
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON TABLE "{table}" FROM anon, authenticated')


def downgrade():
    raise RuntimeError("La reversión requiere preservar las certificaciones privadas y sus decisiones.")
