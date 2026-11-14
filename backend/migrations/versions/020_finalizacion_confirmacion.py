"""HU-34 ST-01: Agregar campos de finalización reportada y vencimiento de confirmación."""
from alembic import op
import sqlalchemy as sa

revision = "020_finalizacion_confirmacion"
down_revision = "019_certificaciones_oficio"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("solicitudes_directas") as batch_op:
        batch_op.add_column(
            sa.Column("finalizacion_reportada_en", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("vencimiento_confirmacion_en", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "pendiente_recepcion",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )
    op.create_index(
        "ix_solicitudes_directas_vencimiento_confirmacion",
        "solicitudes_directas",
        ["vencimiento_confirmacion_en"],
    )

    with op.batch_alter_table("solicitudes_abiertas") as batch_op:
        batch_op.add_column(
            sa.Column("finalizacion_reportada_en", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("vencimiento_confirmacion_en", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "pendiente_recepcion",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            )
        )
    op.create_index(
        "ix_solicitudes_abiertas_vencimiento_confirmacion",
        "solicitudes_abiertas",
        ["vencimiento_confirmacion_en"],
    )


def downgrade():
    op.drop_index(
        "ix_solicitudes_abiertas_vencimiento_confirmacion",
        table_name="solicitudes_abiertas",
    )
    with op.batch_alter_table("solicitudes_abiertas") as batch_op:
        batch_op.drop_column("pendiente_recepcion")
        batch_op.drop_column("vencimiento_confirmacion_en")
        batch_op.drop_column("finalizacion_reportada_en")

    op.drop_index(
        "ix_solicitudes_directas_vencimiento_confirmacion",
        table_name="solicitudes_directas",
    )
    with op.batch_alter_table("solicitudes_directas") as batch_op:
        batch_op.drop_column("pendiente_recepcion")
        batch_op.drop_column("vencimiento_confirmacion_en")
        batch_op.drop_column("finalizacion_reportada_en")
