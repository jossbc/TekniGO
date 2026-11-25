"""HU-47: Bandeja de notificaciones persistente, outbox de eventos e idempotencia."""
from alembic import op
import sqlalchemy as sa

revision = "035_bandeja_notificaciones"
down_revision = "034_resenas_servicio"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "eventos_notificacion",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clave_evento", sa.String(length=180), nullable=False),
        sa.Column("tipo_evento", sa.String(length=64), nullable=False),
        sa.Column("payload_seguro", sa.JSON(), nullable=False),
        sa.Column("estado", sa.String(length=20), nullable=False, server_default="PENDIENTE"),
        sa.Column("intentos", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ultimo_error", sa.Text(), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("procesado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("clave_evento", name="uq_eventos_notificacion_clave_evento"),
        sa.CheckConstraint(
            "estado IN ('PENDIENTE', 'PROCESADO', 'FALLIDO')",
            name="ck_evento_notificacion_estado",
        ),
        sa.CheckConstraint("intentos >= 0", name="ck_evento_notificacion_intentos"),
        sa.CheckConstraint(
            "length(trim(clave_evento)) > 0 AND length(trim(tipo_evento)) > 0",
            name="ck_evento_notificacion_claves",
        ),
    )
    op.create_index("ix_eventos_notificacion_estado_creado", "eventos_notificacion", ["estado", "creado_en"])
    op.create_index("ix_eventos_notificacion_tipo", "eventos_notificacion", ["tipo_evento"])

    op.create_table(
        "notificaciones_bandeja",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("evento_id", sa.Uuid(), sa.ForeignKey("eventos_notificacion.id"), nullable=False),
        sa.Column("destinatario_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("clave_idempotencia", sa.String(length=200), nullable=False),
        sa.Column("tipo_evento", sa.String(length=64), nullable=False),
        sa.Column("titulo", sa.String(length=160), nullable=False),
        sa.Column("resumen", sa.Text(), nullable=False),
        sa.Column("enlace_ruta", sa.String(length=240), nullable=False),
        sa.Column("recurso_tipo", sa.String(length=50), nullable=True),
        sa.Column("recurso_id", sa.String(length=64), nullable=True),
        sa.Column("metadatos_seguros", sa.JSON(), nullable=False),
        sa.Column("leida", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("leida_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("creada_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "destinatario_id",
            "clave_idempotencia",
            name="uq_notificacion_destinatario_idempotencia",
        ),
        sa.UniqueConstraint(
            "evento_id",
            "destinatario_id",
            name="uq_notificacion_evento_destinatario",
        ),
        sa.CheckConstraint(
            "length(trim(titulo)) > 0 AND length(trim(resumen)) > 0 AND length(trim(enlace_ruta)) > 0",
            name="ck_notificacion_campos_no_vacios",
        ),
        sa.CheckConstraint("enlace_ruta LIKE '/%'", name="ck_notificacion_enlace_interno"),
    )
    op.create_index(
        "ix_notificaciones_destinatario_creada",
        "notificaciones_bandeja",
        ["destinatario_id", "creada_en", "id"],
    )
    op.create_index(
        "ix_notificaciones_destinatario_leida",
        "notificaciones_bandeja",
        ["destinatario_id", "leida", "creada_en"],
    )

    for table in ("eventos_notificacion", "notificaciones_bandeja"):
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON TABLE "{table}" FROM anon, authenticated')


def downgrade():
    op.drop_index("ix_notificaciones_destinatario_leida", table_name="notificaciones_bandeja")
    op.drop_index("ix_notificaciones_destinatario_creada", table_name="notificaciones_bandeja")
    op.drop_table("notificaciones_bandeja")
    op.drop_index("ix_eventos_notificacion_tipo", table_name="eventos_notificacion")
    op.drop_index("ix_eventos_notificacion_estado_creado", table_name="eventos_notificacion")
    op.drop_table("eventos_notificacion")
