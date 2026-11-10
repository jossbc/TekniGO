"""018_propuestas_licitacion"""
from alembic import op
import sqlalchemy as sa

revision = "018_propuestas_licitacion"
down_revision = "017_descartes_licitacion"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "propuestas_solicitudes_abiertas",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("solicitud_id", sa.String(), nullable=False),
        sa.Column("usuario_id", sa.Uuid(), nullable=False),
        sa.Column("tecnico_id", sa.String(length=128), nullable=False),
        sa.Column("tipo", sa.String(length=30), nullable=False, server_default="ACEPTACION_PRESUPUESTO"),
        sa.Column("monto", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("justificacion", sa.Text(), nullable=True),
        sa.Column("estado", sa.String(length=30), nullable=False, server_default="PENDIENTE"),
        sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actualizada_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["solicitud_id"], ["solicitudes_abiertas.id"]),
        sa.ForeignKeyConstraint(["usuario_id"], ["perfiles_tecnicos.usuario_id"]),
        sa.ForeignKeyConstraint(["tecnico_id"], ["usuarios.firebase_uid"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("solicitud_id", "usuario_id", name="uq_propuesta_activa_por_tecnico_y_licitacion"),
        sa.CheckConstraint(
            "estado IN ('PENDIENTE', 'ACEPTADA', 'RECHAZADA', 'CANCELADA', 'RETIRADA')",
            name="ck_propuesta_solicitud_abierta_estado",
        ),
        sa.CheckConstraint("monto > 0", name="ck_propuesta_solicitud_abierta_monto"),
    )
    op.create_index(
        "ix_propuestas_solicitudes_abiertas_solicitud_id",
        "propuestas_solicitudes_abiertas",
        ["solicitud_id"],
    )
    op.create_index(
        "ix_propuestas_solicitudes_abiertas_usuario_id",
        "propuestas_solicitudes_abiertas",
        ["usuario_id"],
    )
    op.create_index(
        "ix_propuestas_solicitudes_abiertas_tecnico_id",
        "propuestas_solicitudes_abiertas",
        ["tecnico_id"],
    )


def downgrade():
    op.drop_index("ix_propuestas_solicitudes_abiertas_tecnico_id", table_name="propuestas_solicitudes_abiertas")
    op.drop_index("ix_propuestas_solicitudes_abiertas_usuario_id", table_name="propuestas_solicitudes_abiertas")
    op.drop_index("ix_propuestas_solicitudes_abiertas_solicitud_id", table_name="propuestas_solicitudes_abiertas")
    op.drop_table("propuestas_solicitudes_abiertas")
