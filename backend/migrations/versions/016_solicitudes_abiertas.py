"""016_solicitudes_abiertas"""
from alembic import op
import sqlalchemy as sa

revision = "016_solicitudes_abiertas"
down_revision = "de1f7a2e9dd6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "solicitudes_abiertas",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("cliente_id", sa.String(), nullable=False),
        sa.Column("tecnico_id", sa.String(), nullable=True),
        sa.Column("especialidad_id", sa.Uuid(), nullable=False),
        sa.Column("servicio_id", sa.Uuid(), nullable=True),
        sa.Column("modalidad", sa.String(length=20), nullable=False, server_default="ABIERTA"),
        sa.Column("municipio", sa.String(length=80), nullable=False),
        sa.Column("sector", sa.String(length=100), nullable=False),
        sa.Column("direccion_exacta", sa.Text(), nullable=False),
        sa.Column("fecha_hora", sa.DateTime(timezone=True), nullable=False),
        sa.Column("descripcion", sa.Text(), nullable=False),
        sa.Column("presupuesto", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("monto_base", sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column("metodo_pago", sa.String(length=20), nullable=False),
        sa.Column("estado", sa.String(length=30), nullable=False, server_default="Publicada"),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["cliente_id"], ["usuarios.firebase_uid"]),
        sa.ForeignKeyConstraint(["tecnico_id"], ["usuarios.firebase_uid"]),
        sa.ForeignKeyConstraint(["especialidad_id"], ["especialidades.id"]),
        sa.ForeignKeyConstraint(["servicio_id"], ["servicios.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_solicitudes_abiertas_cliente_id", "solicitudes_abiertas", ["cliente_id"])
    op.create_index("ix_solicitudes_abiertas_especialidad_id", "solicitudes_abiertas", ["especialidad_id"])


def downgrade():
    op.drop_index("ix_solicitudes_abiertas_especialidad_id", table_name="solicitudes_abiertas")
    op.drop_index("ix_solicitudes_abiertas_cliente_id", table_name="solicitudes_abiertas")
    op.drop_table("solicitudes_abiertas")
