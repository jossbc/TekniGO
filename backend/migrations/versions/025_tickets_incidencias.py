"""Persist incident tickets and immutable moderation history."""
import sqlalchemy as sa
from alembic import op

revision = "025_tickets_incidencias"
down_revision = "024_recepcion_persistente"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tickets_incidencias",
        sa.Column("ticket_id", sa.String(length=40), primary_key=True),
        sa.Column("solicitud_id", sa.String(length=64), nullable=False),
        sa.Column("modalidad", sa.String(length=20), nullable=False),
        sa.Column("reportante_uid", sa.String(length=128), nullable=False),
        sa.Column("contraparte_uid", sa.String(length=128), nullable=True),
        sa.Column("rol_reportante", sa.String(length=20), nullable=False),
        sa.Column("tipo", sa.String(length=50), nullable=False),
        sa.Column("prioridad", sa.String(length=20), nullable=False, server_default="ALTA"),
        sa.Column("estado", sa.String(length=20), nullable=False, server_default="ABIERTO"),
        sa.Column("es_apelacion_justificada", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("falta_computada", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("bloquea_cierre_automatico", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("bloquea_liberacion_fondos", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_tickets_incidencias_solicitud_id", "tickets_incidencias", ["solicitud_id"])
    op.create_index("ix_tickets_incidencias_reportante_uid", "tickets_incidencias", ["reportante_uid"])
    op.create_index("ix_tickets_incidencias_contraparte_uid", "tickets_incidencias", ["contraparte_uid"])
    op.create_index("ix_tickets_incidencias_estado", "tickets_incidencias", ["estado"])
    op.create_index("ix_tickets_incidencias_solicitud_estado", "tickets_incidencias", ["solicitud_id", "estado"])


def downgrade():
    op.drop_index("ix_tickets_incidencias_solicitud_estado", table_name="tickets_incidencias")
    op.drop_index("ix_tickets_incidencias_estado", table_name="tickets_incidencias")
    op.drop_index("ix_tickets_incidencias_contraparte_uid", table_name="tickets_incidencias")
    op.drop_index("ix_tickets_incidencias_reportante_uid", table_name="tickets_incidencias")
    op.drop_index("ix_tickets_incidencias_solicitud_id", table_name="tickets_incidencias")
    op.drop_table("tickets_incidencias")