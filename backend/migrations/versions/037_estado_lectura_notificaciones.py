"""HU-49 ST-01: agregar estado_lectura y coherencia con fecha de lectura en notificaciones."""
from alembic import op
import sqlalchemy as sa

revision = "037_lectura_notificaciones"
down_revision = "036_eventos_reincidencia"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "notificaciones_bandeja",
        sa.Column(
            "estado_lectura",
            sa.String(length=16),
            nullable=False,
            server_default="PENDIENTE",
        ),
    )

    op.execute(
        "UPDATE notificaciones_bandeja "
        "SET leida_en = COALESCE(creada_en, CURRENT_TIMESTAMP) "
        "WHERE leida = true AND leida_en IS NULL"
    )
    op.execute(
        "UPDATE notificaciones_bandeja "
        "SET leida_en = NULL "
        "WHERE leida = false AND leida_en IS NOT NULL"
    )
    op.execute(
        "UPDATE notificaciones_bandeja "
        "SET estado_lectura = CASE WHEN leida = true THEN 'LEIDA' ELSE 'PENDIENTE' END"
    )

    op.create_check_constraint(
        "ck_notificacion_estado_lectura",
        "notificaciones_bandeja",
        "estado_lectura IN ('PENDIENTE', 'LEIDA')",
    )
    op.create_check_constraint(
        "ck_notificacion_lectura_coherente",
        "notificaciones_bandeja",
        "((estado_lectura = 'PENDIENTE' AND leida = false AND leida_en IS NULL) "
        "OR (estado_lectura = 'LEIDA' AND leida = true AND leida_en IS NOT NULL))",
    )
    op.create_index(
        "ix_notificaciones_destinatario_estado_creada",
        "notificaciones_bandeja",
        ["destinatario_id", "estado_lectura", "creada_en", "id"],
    )


def downgrade():
    op.drop_index(
        "ix_notificaciones_destinatario_estado_creada",
        table_name="notificaciones_bandeja",
    )
    op.drop_constraint(
        "ck_notificacion_lectura_coherente",
        "notificaciones_bandeja",
        type_="check",
    )
    op.drop_constraint(
        "ck_notificacion_estado_lectura",
        "notificaciones_bandeja",
        type_="check",
    )
    op.drop_column("notificaciones_bandeja", "estado_lectura")
