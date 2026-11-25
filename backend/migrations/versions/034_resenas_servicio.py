"""Reviews and reputation system (HU-44, HU-45, HU-46)."""
from alembic import op
import sqlalchemy as sa

revision = "034_resenas_servicio"
down_revision = "033_chat_contratacion"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "resenas_servicio",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tipo_solicitud", sa.String(length=20), nullable=False),
        sa.Column("solicitud_id", sa.String(length=64), nullable=False),
        sa.Column("evaluador_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("evaluado_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("rol_evaluador", sa.String(length=20), nullable=False),
        sa.Column("puntuacion", sa.Integer(), nullable=False),
        sa.Column("comentario", sa.Text(), nullable=False),
        sa.Column("aspectos_destacados", sa.JSON(), nullable=True),
        sa.Column("estado_publicacion", sa.String(length=20), nullable=False, server_default="PENDIENTE"),
        sa.Column("fecha_completado_servicio", sa.DateTime(timezone=True), nullable=False),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("publicada_en", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("tipo_solicitud IN ('DIRECTA', 'ABIERTA')", name="ck_resena_tipo_solicitud"),
        sa.CheckConstraint("rol_evaluador IN ('CLIENTE', 'TECNICO')", name="ck_resena_rol_evaluador"),
        sa.CheckConstraint("puntuacion >= 1 AND puntuacion <= 5", name="ck_resena_puntuacion"),
        sa.CheckConstraint("estado_publicacion IN ('PENDIENTE', 'PUBLICADA')", name="ck_resena_estado_publicacion"),
        sa.CheckConstraint("length(trim(solicitud_id)) > 0", name="ck_resena_solicitud_id"),
        sa.CheckConstraint("length(trim(comentario)) > 0", name="ck_resena_comentario"),
        sa.UniqueConstraint("tipo_solicitud", "solicitud_id", "rol_evaluador", name="uq_resena_por_solicitud_y_rol"),
    )
    op.create_index("ix_resenas_evaluado_publicacion", "resenas_servicio", ["evaluado_id", "estado_publicacion"])
    op.create_index("ix_resenas_solicitud", "resenas_servicio", ["tipo_solicitud", "solicitud_id"])

    op.execute('ALTER TABLE "resenas_servicio" ENABLE ROW LEVEL SECURITY')
    op.execute('REVOKE ALL ON TABLE "resenas_servicio" FROM anon, authenticated')


def downgrade():
    op.drop_index("ix_resenas_solicitud", table_name="resenas_servicio")
    op.drop_index("ix_resenas_evaluado_publicacion", table_name="resenas_servicio")
    op.drop_table("resenas_servicio")
