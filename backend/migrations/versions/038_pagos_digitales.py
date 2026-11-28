"""HU-38: intentos de pago digital, webhooks idempotentes y soporte de conciliación en reembolsos."""
from alembic import op
import sqlalchemy as sa

revision = "038_pagos_digitales"
down_revision = "037_lectura_notificaciones"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "intentos_pago_digital",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("clave_idempotencia", sa.String(length=160), nullable=False),
        sa.Column("referencia_interna", sa.String(length=80), nullable=False),
        sa.Column("modalidad", sa.String(length=10), nullable=False),
        sa.Column("solicitud_id", sa.String(length=36), nullable=False),
        sa.Column("concepto", sa.String(length=16), nullable=False, server_default="BASE"),
        sa.Column("cliente_uid", sa.String(length=128), nullable=False),
        sa.Column("tecnico_uid", sa.String(length=128), nullable=True),
        sa.Column("propuesta_id", sa.String(length=36), nullable=True),
        sa.Column("moneda", sa.String(length=5), nullable=False, server_default="HNL"),
        sa.Column("monto", sa.Numeric(10, 2), nullable=False),
        sa.Column("proveedor", sa.String(length=32), nullable=False),
        sa.Column("entorno", sa.String(length=24), nullable=False, server_default="SANDBOX"),
        sa.Column("referencia_pasarela", sa.String(length=160), nullable=True),
        sa.Column("payment_intent_id", sa.String(length=160), nullable=True),
        sa.Column("checkout_url", sa.Text(), nullable=True),
        sa.Column("estado", sa.String(length=24), nullable=False, server_default="PENDIENTE"),
        sa.Column("estado_retencion", sa.String(length=24), nullable=False, server_default="NO_APLICA"),
        sa.Column("motivo_resultado", sa.Text(), nullable=True),
        sa.Column("metadatos", sa.JSON(), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("confirmado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("liberado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reembolsado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("clave_idempotencia", name="uq_intento_pago_idempotencia"),
        sa.UniqueConstraint("referencia_interna", name="uq_intento_pago_ref_interna"),
        sa.CheckConstraint("modalidad IN ('DIRECTA', 'ABIERTA')", name="ck_intento_pago_modalidad"),
        sa.CheckConstraint("concepto IN ('BASE', 'AJUSTE')", name="ck_intento_pago_concepto"),
        sa.CheckConstraint("moneda = 'HNL'", name="ck_intento_pago_moneda"),
        sa.CheckConstraint("monto > 0", name="ck_intento_pago_monto_positivo"),
        sa.CheckConstraint("entorno IN ('SANDBOX', 'SIMULADOR_LOCAL')", name="ck_intento_pago_entorno"),
        sa.CheckConstraint(
            "estado IN ('PENDIENTE', 'CONFIRMADO', 'RECHAZADO', 'ABANDONADO', 'EXPIRADO', 'REEMBOLSADO')",
            name="ck_intento_pago_estado",
        ),
        sa.CheckConstraint(
            "estado_retencion IN ('NO_APLICA', 'RETENIDO', 'LIBERADO', 'REEMBOLSADO')",
            name="ck_intento_pago_retencion",
        ),
    )
    op.create_index(
        "ix_intentos_pago_solicitud_concepto",
        "intentos_pago_digital",
        ["modalidad", "solicitud_id", "concepto", "estado"],
    )
    op.create_index("ix_intentos_pago_ref_pasarela", "intentos_pago_digital", ["referencia_pasarela"])
    op.create_index("ix_intentos_pago_pi_id", "intentos_pago_digital", ["payment_intent_id"])

    op.create_table(
        "eventos_webhook_pago",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("proveedor", sa.String(length=32), nullable=False),
        sa.Column("evento_externo_id", sa.String(length=160), nullable=False),
        sa.Column("tipo_evento", sa.String(length=80), nullable=False),
        sa.Column("referencia_pasarela", sa.String(length=160), nullable=True),
        sa.Column("payment_intent_id", sa.String(length=160), nullable=True),
        sa.Column("estado_procesamiento", sa.String(length=24), nullable=False, server_default="PROCESADO"),
        sa.Column("payload_resumen", sa.JSON(), nullable=False),
        sa.Column("recibido_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("proveedor", "evento_externo_id", name="uq_webhook_pago_proveedor_evento"),
        sa.CheckConstraint(
            "estado_procesamiento IN ('PROCESADO', 'OMITIDO', 'ERROR')",
            name="ck_webhook_pago_estado",
        ),
    )
    op.create_index("ix_webhook_pago_ref", "eventos_webhook_pago", ["referencia_pasarela"])

    op.add_column(
        "reembolsos_solicitud",
        sa.Column("intentos", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("reembolsos_solicitud", sa.Column("ultimo_error", sa.Text(), nullable=True))
    op.add_column(
        "reembolsos_solicitud",
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("ck_reembolso_estado_pasarela", "reembolsos_solicitud", type_="check")
    op.create_check_constraint(
        "ck_reembolso_estado_pasarela",
        "reembolsos_solicitud",
        "estado_pasarela IN ('EN_PROCESO_PASARELA', 'PENDIENTE_CONCILIACION', 'CONFIRMADO_PASARELA')",
    )

    if op.get_bind().dialect.name == "postgresql":
        for table in ("intentos_pago_digital", "eventos_webhook_pago"):
            op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
            op.execute(f'REVOKE ALL ON TABLE "{table}" FROM anon, authenticated')


def downgrade():
    op.drop_constraint("ck_reembolso_estado_pasarela", "reembolsos_solicitud", type_="check")
    op.create_check_constraint(
        "ck_reembolso_estado_pasarela",
        "reembolsos_solicitud",
        "estado_pasarela IN ('EN_PROCESO_PASARELA', 'CONFIRMADO_PASARELA')",
    )
    op.drop_column("reembolsos_solicitud", "actualizado_en")
    op.drop_column("reembolsos_solicitud", "ultimo_error")
    op.drop_column("reembolsos_solicitud", "intentos")

    op.drop_index("ix_webhook_pago_ref", table_name="eventos_webhook_pago")
    op.drop_table("eventos_webhook_pago")

    op.drop_index("ix_intentos_pago_pi_id", table_name="intentos_pago_digital")
    op.drop_index("ix_intentos_pago_ref_pasarela", table_name="intentos_pago_digital")
    op.drop_index("ix_intentos_pago_solicitud_concepto", table_name="intentos_pago_digital")
    op.drop_table("intentos_pago_digital")