"""017_descartes_licitacion"""
from alembic import op
import sqlalchemy as sa

revision = "017_descartes_licitacion"
down_revision = "016_solicitudes_abiertas"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "solicitudes_abiertas_descartadas",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("usuario_id", sa.Uuid(), nullable=False),
        sa.Column("tecnico_id", sa.String(length=128), nullable=False),
        sa.Column("solicitud_id", sa.String(), nullable=False),
        sa.Column("descartada_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["usuario_id"], ["perfiles_tecnicos.usuario_id"]),
        sa.ForeignKeyConstraint(["tecnico_id"], ["usuarios.firebase_uid"]),
        sa.ForeignKeyConstraint(["solicitud_id"], ["solicitudes_abiertas.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("usuario_id", "solicitud_id", name="uq_solicitud_abierta_descartada_tecnico"),
    )
    op.create_index(
        "ix_solicitudes_abiertas_descartadas_usuario_id",
        "solicitudes_abiertas_descartadas",
        ["usuario_id"],
    )
    op.create_index(
        "ix_solicitudes_abiertas_descartadas_tecnico_id",
        "solicitudes_abiertas_descartadas",
        ["tecnico_id"],
    )
    op.create_index(
        "ix_solicitudes_abiertas_descartadas_solicitud_id",
        "solicitudes_abiertas_descartadas",
        ["solicitud_id"],
    )


def downgrade():
    op.drop_index("ix_solicitudes_abiertas_descartadas_solicitud_id", table_name="solicitudes_abiertas_descartadas")
    op.drop_index("ix_solicitudes_abiertas_descartadas_tecnico_id", table_name="solicitudes_abiertas_descartadas")
    op.drop_index("ix_solicitudes_abiertas_descartadas_usuario_id", table_name="solicitudes_abiertas_descartadas")
    op.drop_table("solicitudes_abiertas_descartadas")
