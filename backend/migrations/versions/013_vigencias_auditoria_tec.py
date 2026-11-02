"""Agregar vigencias y auditoría de transiciones para activación de técnicos (HU-18 ST-02)."""
from alembic import op
import sqlalchemy as sa

revision = "013_vigencias_auditoria_tec"
down_revision = "012_documentos_tecnico"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("usuarios") as batch_op:
        batch_op.add_column(
            sa.Column("telefono_verificado", sa.Boolean(), nullable=False, server_default=sa.text("false"))
        )

    with op.batch_alter_table("documentos_identidad") as batch_op:
        batch_op.add_column(sa.Column("fecha_vencimiento", sa.Date(), nullable=True))
        batch_op.drop_constraint("ck_documento_identidad_estado", type_="check")
        batch_op.create_check_constraint(
            "ck_documento_identidad_estado",
            "estado IN ('PENDIENTE', 'APROBADO', 'RECHAZADO', 'VENCIDO', 'INVALIDADO')",
        )

    with op.batch_alter_table("perfiles_tecnicos") as batch_op:
        batch_op.add_column(sa.Column("verificado_en", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("motivo_restriccion", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("suspendido", sa.Boolean(), nullable=False, server_default=sa.text("false"))
        )
        batch_op.add_column(
            sa.Column("restriccion_financiera", sa.Boolean(), nullable=False, server_default=sa.text("false"))
        )
        batch_op.add_column(
            sa.Column("estado_certificacion", sa.String(length=30), nullable=False, server_default="NO_CERTIFICADO")
        )
        batch_op.add_column(sa.Column("calificacion_promedio", sa.Numeric(precision=3, scale=2), nullable=True))
        batch_op.create_check_constraint(
            "ck_perfil_tecnico_estado_verificacion",
            "estado_verificacion IN ('NO_VERIFICADO', 'PENDIENTE', 'EN_REVISION', 'VERIFICADO', 'RECHAZADO', 'RESTRINGIDO')",
        )

    op.create_table(
        "historial_estados_tecnico",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("perfiles_tecnicos.usuario_id"), nullable=False),
        sa.Column("estado_anterior", sa.String(length=30), nullable=False),
        sa.Column("estado_nuevo", sa.String(length=30), nullable=False),
        sa.Column("habilitado_anterior", sa.Boolean(), nullable=False),
        sa.Column("habilitado_nuevo", sa.Boolean(), nullable=False),
        sa.Column("causa", sa.Text(), nullable=False),
        sa.Column("actor_usuario_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=True),
        sa.Column("proceso_responsable", sa.String(length=100), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_historial_estados_tecnico_usuario_id",
        "historial_estados_tecnico",
        ["usuario_id"],
    )
    op.execute(sa.text('ALTER TABLE "historial_estados_tecnico" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text('REVOKE ALL ON TABLE "historial_estados_tecnico" FROM anon, authenticated'))


def downgrade():
    raise RuntimeError(
        "La reversión requiere preservar el historial de auditoría de transiciones de estado del técnico."
    )

