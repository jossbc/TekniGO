"""HU-36 ST-06: tabla de eventos de tercera reincidencia de faltas no justificadas."""
import sqlalchemy as sa
from alembic import op

revision = "036_eventos_reincidencia"
down_revision = "035_bandeja_notificaciones"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "eventos_reincidencia_faltas",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("clave_idempotencia", sa.String(160), nullable=False),
        sa.Column("usuario_uid", sa.String(128), nullable=False),
        sa.Column("rol_infractor", sa.String(20), nullable=True),
        sa.Column("umbral_faltas", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("total_faltas_computadas", sa.Integer(), nullable=False),
        sa.Column("faltas_ids", sa.JSON(), nullable=False),
        sa.Column("cancelacion_disparadora_id", sa.Integer(), sa.ForeignKey("cancelaciones_solicitud.id"), nullable=True),
        sa.Column("solicitud_disparadora_id", sa.String(36), nullable=True),
        sa.Column("estado", sa.String(32), nullable=False, server_default="PENDIENTE_DECISION_HU52"),
        sa.Column("requiere_decision_hu52", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("suspension_automatica_aplicada", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("generado_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("clave_idempotencia", name="uq_reincidencia_clave_idempotencia"),
        sa.CheckConstraint("umbral_faltas >= 1", name="ck_reincidencia_umbral_positivo"),
        sa.CheckConstraint("total_faltas_computadas >= 0", name="ck_reincidencia_total_no_negativo"),
        sa.CheckConstraint(
            "estado IN ('PENDIENTE_DECISION_HU52', 'EVALUADO_HU52', 'INVALIDADO_POR_JUSTIFICACION')",
            name="ck_reincidencia_estado",
        ),
    )
    op.create_index(
        "ix_reincidencia_usuario_estado",
        "eventos_reincidencia_faltas",
        ["usuario_uid", "estado"],
    )


def downgrade():
    op.drop_index("ix_reincidencia_usuario_estado", table_name="eventos_reincidencia_faltas")
    op.drop_table("eventos_reincidencia_faltas")