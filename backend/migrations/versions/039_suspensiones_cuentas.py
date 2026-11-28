"""HU-52 ST-01: suspensiones de cuenta, causas, vigencias, auditoría, baja lógica documental e identidades bloqueadas."""
from alembic import op
import sqlalchemy as sa

revision = "039_suspensiones_cuentas"
down_revision = "038_pagos_digitales"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("usuarios") as batch_op:
        batch_op.add_column(
            sa.Column("suspendido", sa.Boolean(), nullable=False, server_default=sa.text("false"))
        )
        batch_op.add_column(sa.Column("suspension_tipo", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("suspension_vence_en", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("cierre_cuenta_en", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("motivo_cierre_cuenta", sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            "ck_usuario_suspension_tipo",
            "suspension_tipo IS NULL OR suspension_tipo IN ('TEMPORAL', 'DEFINITIVA')",
        )

    for table_name in ("documentos_identidad", "certificaciones_oficio"):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.add_column(
                sa.Column("baja_logica", sa.Boolean(), nullable=False, server_default=sa.text("false"))
            )
            batch_op.add_column(sa.Column("baja_logica_en", sa.DateTime(timezone=True), nullable=True))
            batch_op.add_column(sa.Column("baja_logica_motivo", sa.Text(), nullable=True))
            batch_op.add_column(
                sa.Column("acceso_operativo_revocado", sa.Boolean(), nullable=False, server_default=sa.text("false"))
            )

    op.create_table(
        "suspensiones_cuenta",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("usuario_uid", sa.String(length=128), nullable=False),
        sa.Column("tipo", sa.String(length=20), nullable=False),
        sa.Column("causa", sa.String(length=40), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("estado", sa.String(length=20), nullable=False, server_default="ACTIVA"),
        sa.Column("responsable_admin_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("responsable_admin_uid", sa.String(length=128), nullable=False),
        sa.Column("iniciada_en", sa.DateTime(timezone=True), nullable=False),
        sa.Column("vence_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("levantada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("levantada_por_admin_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=True),
        sa.Column("motivo_levantamiento", sa.Text(), nullable=True),
        sa.Column("expirada_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "alerta_reincidencia_id",
            sa.String(length=36),
            sa.ForeignKey("eventos_reincidencia_faltas.id"),
            nullable=True,
        ),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("tipo IN ('TEMPORAL', 'DEFINITIVA')", name="ck_suspension_cuenta_tipo"),
        sa.CheckConstraint(
            "causa IN ('CONDUCTA', 'REINCIDENCIA_FALTAS', 'INCUMPLIMIENTO_REGLAS', 'FRAUDE_IDENTIDAD', 'INCIDENCIA_GRAVE')",
            name="ck_suspension_cuenta_causa",
        ),
        sa.CheckConstraint("estado IN ('ACTIVA', 'LEVANTADA', 'EXPIRADA')", name="ck_suspension_cuenta_estado"),
        sa.CheckConstraint("length(trim(motivo)) >= 5", name="ck_suspension_cuenta_motivo"),
        sa.CheckConstraint(
            "((tipo = 'TEMPORAL' AND vence_en IS NOT NULL AND vence_en > iniciada_en) "
            "OR (tipo = 'DEFINITIVA' AND vence_en IS NULL))",
            name="ck_suspension_cuenta_vigencia",
        ),
    )
    op.create_index("ix_suspensiones_cuenta_usuario_id", "suspensiones_cuenta", ["usuario_id"])
    op.create_index("ix_suspensiones_cuenta_usuario_uid", "suspensiones_cuenta", ["usuario_uid"])
    op.create_index("ix_suspensiones_cuenta_usuario_estado", "suspensiones_cuenta", ["usuario_id", "estado"])
    op.create_index(
        "ix_suspensiones_cuenta_estado_vencimiento",
        "suspensiones_cuenta",
        ["estado", "tipo", "vence_en"],
    )

    op.create_table(
        "auditoria_suspensiones_cuenta",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("suspension_id", sa.Uuid(), sa.ForeignKey("suspensiones_cuenta.id"), nullable=True),
        sa.Column("evento", sa.String(length=40), nullable=False),
        sa.Column("tipo_suspension", sa.String(length=20), nullable=True),
        sa.Column("causa", sa.String(length=40), nullable=True),
        sa.Column("estado_anterior", sa.String(length=30), nullable=True),
        sa.Column("estado_nuevo", sa.String(length=30), nullable=True),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("actor_admin_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=True),
        sa.Column("proceso_responsable", sa.String(length=80), nullable=False),
        sa.Column("metadatos", sa.JSON(), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "evento IN ('SUSPENSION_APLICADA', 'SUSPENSION_LEVANTADA', 'SUSPENSION_EXPIRADA', "
            "'ALERTA_REINCIDENCIA_ATENDIDA', 'CIERRE_CUENTA_APLICADO', 'BAJA_LOGICA_DOCUMENTAL')",
            name="ck_auditoria_suspension_evento",
        ),
    )
    op.create_index("ix_auditoria_suspensiones_usuario_id", "auditoria_suspensiones_cuenta", ["usuario_id"])
    op.create_index("ix_auditoria_suspensiones_suspension_id", "auditoria_suspensiones_cuenta", ["suspension_id"])
    op.create_index(
        "ix_auditoria_suspensiones_usuario_creado",
        "auditoria_suspensiones_cuenta",
        ["usuario_id", "creado_en"],
    )

    op.create_table(
        "identidades_bloqueadas",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("tipo_identificador", sa.String(length=20), nullable=False),
        sa.Column("valor_hash", sa.String(length=64), nullable=False),
        sa.Column("motivo_bloqueo", sa.String(length=30), nullable=False),
        sa.Column("activo", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tipo_identificador", "valor_hash", name="uq_identidad_bloqueada_tipo_hash"),
        sa.CheckConstraint("tipo_identificador IN ('DNI', 'TELEFONO')", name="ck_identidad_bloqueada_tipo"),
        sa.CheckConstraint(
            "motivo_bloqueo IN ('SUSPENSION_ACTIVA', 'CIERRE_CUENTA')",
            name="ck_identidad_bloqueada_motivo",
        ),
    )
    op.create_index("ix_identidades_bloqueadas_usuario_id", "identidades_bloqueadas", ["usuario_id"])
    op.create_index(
        "ix_identidades_bloqueadas_usuario_activo",
        "identidades_bloqueadas",
        ["usuario_id", "activo"],
    )


def downgrade():
    op.drop_index("ix_identidades_bloqueadas_usuario_activo", table_name="identidades_bloqueadas")
    op.drop_index("ix_identidades_bloqueadas_usuario_id", table_name="identidades_bloqueadas")
    op.drop_table("identidades_bloqueadas")

    op.drop_index("ix_auditoria_suspensiones_usuario_creado", table_name="auditoria_suspensiones_cuenta")
    op.drop_index("ix_auditoria_suspensiones_suspension_id", table_name="auditoria_suspensiones_cuenta")
    op.drop_index("ix_auditoria_suspensiones_usuario_id", table_name="auditoria_suspensiones_cuenta")
    op.drop_table("auditoria_suspensiones_cuenta")

    op.drop_index("ix_suspensiones_cuenta_estado_vencimiento", table_name="suspensiones_cuenta")
    op.drop_index("ix_suspensiones_cuenta_usuario_estado", table_name="suspensiones_cuenta")
    op.drop_index("ix_suspensiones_cuenta_usuario_uid", table_name="suspensiones_cuenta")
    op.drop_index("ix_suspensiones_cuenta_usuario_id", table_name="suspensiones_cuenta")
    op.drop_table("suspensiones_cuenta")

    for table_name in ("certificaciones_oficio", "documentos_identidad"):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_column("acceso_operativo_revocado")
            batch_op.drop_column("baja_logica_motivo")
            batch_op.drop_column("baja_logica_en")
            batch_op.drop_column("baja_logica")

    with op.batch_alter_table("usuarios") as batch_op:
        batch_op.drop_constraint("ck_usuario_suspension_tipo", type_="check")
        batch_op.drop_column("motivo_cierre_cuenta")
        batch_op.drop_column("cierre_cuenta_en")
        batch_op.drop_column("suspension_vence_en")
        batch_op.drop_column("suspension_tipo")
        batch_op.drop_column("suspendido")