"""HU-43 ST-01: cuenta bancaria privada del tecnico y ordenes de desembolso con reservas."""
import sqlalchemy as sa
from alembic import op

revision = "032_desembolsos_tecnico"
down_revision = "031_liquidaciones_comisiones"
branch_labels = None
depends_on = None

_PAID_WITH_REF = sa.text("estado = 'PAGADA' AND referencia_normalizada IS NOT NULL")


def upgrade():
    op.create_table(
        "cuentas_bancarias_tecnico",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tecnico_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("banco", sa.String(length=80), nullable=False),
        sa.Column("tipo_cuenta", sa.String(length=20), nullable=False),
        sa.Column("titular_nombre", sa.String(length=150), nullable=False),
        sa.Column("numero_cuenta_cifrado", sa.Text(), nullable=False),
        sa.Column("ultimos_cuatro", sa.String(length=4), nullable=False),
        sa.Column("cuenta_enmascarada", sa.String(length=32), nullable=False),
        sa.Column("numero_cuenta_hash", sa.String(length=64), nullable=False),
        sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("actualizado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("tipo_cuenta IN ('AHORRO', 'CHEQUES')", name="ck_cuenta_bancaria_tipo"),
        sa.CheckConstraint("length(trim(banco)) > 0", name="ck_cuenta_bancaria_banco"),
        sa.CheckConstraint("length(trim(titular_nombre)) > 0", name="ck_cuenta_bancaria_titular"),
        sa.CheckConstraint("length(ultimos_cuatro) = 4", name="ck_cuenta_bancaria_ultimos_cuatro"),
    )
    op.create_index(
        "uq_cuenta_bancaria_activa_tecnico",
        "cuentas_bancarias_tecnico",
        ["tecnico_id"],
        unique=True,
        postgresql_where=sa.text("activa = true"),
        sqlite_where=sa.text("activa = 1"),
    )
    op.create_index("ix_cuentas_bancarias_tecnico_id", "cuentas_bancarias_tecnico", ["tecnico_id"])

    op.create_table(
        "ordenes_desembolso",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tecnico_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("cuenta_bancaria_id", sa.Uuid(), sa.ForeignKey("cuentas_bancarias_tecnico.id"), nullable=False),
        sa.Column("importe", sa.Numeric(10, 2), nullable=False),
        sa.Column("importe_reservado", sa.Numeric(10, 2), nullable=False),
        sa.Column("minimo_aplicado", sa.Numeric(10, 2), nullable=False),
        sa.Column("estado", sa.String(length=20), nullable=False, server_default="PENDIENTE"),
        sa.Column("banco_snapshot", sa.String(length=80), nullable=False),
        sa.Column("tipo_cuenta_snapshot", sa.String(length=20), nullable=False),
        sa.Column("cuenta_enmascarada_snapshot", sa.String(length=32), nullable=False),
        sa.Column("referencia_transferencia", sa.String(length=64), nullable=True),
        sa.Column("referencia_normalizada", sa.String(length=64), nullable=True),
        sa.Column("motivo_rechazo", sa.Text(), nullable=True),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.Column("revisado_por", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=True),
        sa.Column("revisado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("estado IN ('PENDIENTE', 'PAGADA', 'RECHAZADA')", name="ck_desembolso_estado"),
        sa.CheckConstraint("importe > 0", name="ck_desembolso_importe"),
        sa.CheckConstraint("importe_reservado >= 0", name="ck_desembolso_importe_reservado"),
        sa.CheckConstraint("minimo_aplicado > 0", name="ck_desembolso_minimo_aplicado"),
        sa.CheckConstraint(
            "(estado = 'PENDIENTE' AND importe_reservado = importe) OR (estado IN ('PAGADA', 'RECHAZADA') AND importe_reservado = 0)",
            name="ck_desembolso_reserva_estado",
        ),
        sa.CheckConstraint(
            "estado = 'PENDIENTE' OR (revisado_por IS NOT NULL AND revisado_en IS NOT NULL)",
            name="ck_desembolso_revision_responsable",
        ),
        sa.CheckConstraint(
            "estado <> 'PAGADA' OR (referencia_transferencia IS NOT NULL AND length(trim(referencia_transferencia)) > 0)",
            name="ck_desembolso_pago_referencia",
        ),
        sa.CheckConstraint(
            "estado <> 'RECHAZADA' OR (motivo_rechazo IS NOT NULL AND length(trim(motivo_rechazo)) > 0)",
            name="ck_desembolso_rechazo_motivo",
        ),
    )
    op.create_index(
        "uq_desembolso_referencia_pagada",
        "ordenes_desembolso",
        ["referencia_normalizada"],
        unique=True,
        postgresql_where=_PAID_WITH_REF,
        sqlite_where=_PAID_WITH_REF,
    )
    op.create_index("ix_desembolsos_tecnico_estado", "ordenes_desembolso", ["tecnico_id", "estado"])

    op.create_table(
        "auditoria_desembolsos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("orden_id", sa.Uuid(), sa.ForeignKey("ordenes_desembolso.id"), nullable=False),
        sa.Column("tecnico_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("accion", sa.String(length=30), nullable=False),
        sa.Column("estado_anterior", sa.String(length=20), nullable=False),
        sa.Column("estado_nuevo", sa.String(length=20), nullable=False),
        sa.Column("importe", sa.Numeric(10, 2), nullable=False),
        sa.Column("referencia_transferencia", sa.String(length=64), nullable=True),
        sa.Column("motivo_rechazo", sa.Text(), nullable=True),
        sa.Column("observacion", sa.Text(), nullable=True),
        sa.Column("saldo_contable_anterior", sa.Numeric(10, 2), nullable=False),
        sa.Column("saldo_contable_posterior", sa.Numeric(10, 2), nullable=False),
        sa.Column("saldo_reservado_anterior", sa.Numeric(10, 2), nullable=False),
        sa.Column("saldo_reservado_posterior", sa.Numeric(10, 2), nullable=False),
        sa.Column("saldo_libre_anterior", sa.Numeric(10, 2), nullable=False),
        sa.Column("saldo_libre_posterior", sa.Numeric(10, 2), nullable=False),
        sa.Column("responsable_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("accion IN ('CONFIRMACION_PAGO', 'RECHAZO')", name="ck_auditoria_desembolso_accion"),
        sa.CheckConstraint(
            "importe > 0 AND saldo_contable_anterior >= 0 AND saldo_contable_posterior >= 0 "
            "AND saldo_reservado_anterior >= 0 AND saldo_reservado_posterior >= 0 "
            "AND saldo_libre_anterior >= 0 AND saldo_libre_posterior >= 0",
            name="ck_auditoria_desembolso_saldos",
        ),
    )
    op.create_index("ix_auditoria_desembolsos_orden", "auditoria_desembolsos", ["orden_id"])
    op.create_index("ix_auditoria_desembolsos_tecnico", "auditoria_desembolsos", ["tecnico_id"])

    op.create_table(
        "movimientos_libro_desembolsos",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("clave_idempotencia", sa.String(length=120), nullable=False, unique=True),
        sa.Column("orden_id", sa.Uuid(), sa.ForeignKey("ordenes_desembolso.id"), nullable=False),
        sa.Column("tecnico_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("tipo_movimiento", sa.String(length=30), nullable=False),
        sa.Column("moneda", sa.String(length=3), nullable=False, server_default="HNL"),
        sa.Column("importe", sa.Numeric(10, 2), nullable=False),
        sa.Column("referencia_transferencia", sa.String(length=64), nullable=True),
        sa.Column("saldo_contable_resultante", sa.Numeric(10, 2), nullable=False),
        sa.Column("saldo_reservado_resultante", sa.Numeric(10, 2), nullable=False),
        sa.Column("saldo_libre_resultante", sa.Numeric(10, 2), nullable=False),
        sa.Column("responsable_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint(
            "tipo_movimiento IN ('RESERVA_DESEMBOLSO', 'DEBITO_DESEMBOLSO', 'LIBERACION_RESERVA')",
            name="ck_libro_desembolso_tipo",
        ),
        sa.CheckConstraint(
            "importe > 0 AND saldo_contable_resultante >= 0 AND saldo_reservado_resultante >= 0 AND saldo_libre_resultante >= 0",
            name="ck_libro_desembolso_importes",
        ),
    )
    op.create_index("ix_libro_desembolsos_orden", "movimientos_libro_desembolsos", ["orden_id"])
    op.create_index("ix_libro_desembolsos_tecnico_tipo", "movimientos_libro_desembolsos", ["tecnico_id", "tipo_movimiento"])


def downgrade():
    op.drop_index("ix_libro_desembolsos_tecnico_tipo", table_name="movimientos_libro_desembolsos")
    op.drop_index("ix_libro_desembolsos_orden", table_name="movimientos_libro_desembolsos")
    op.drop_table("movimientos_libro_desembolsos")
    op.drop_index("ix_auditoria_desembolsos_tecnico", table_name="auditoria_desembolsos")
    op.drop_index("ix_auditoria_desembolsos_orden", table_name="auditoria_desembolsos")
    op.drop_table("auditoria_desembolsos")
    op.drop_index("ix_desembolsos_tecnico_estado", table_name="ordenes_desembolso")
    op.drop_index("uq_desembolso_referencia_pagada", table_name="ordenes_desembolso")
    op.drop_table("ordenes_desembolso")
    op.drop_index("ix_cuentas_bancarias_tecnico_id", table_name="cuentas_bancarias_tecnico")
    op.drop_index("uq_cuenta_bancaria_activa_tecnico", table_name="cuentas_bancarias_tecnico")
    op.drop_table("cuentas_bancarias_tecnico")
