"""Preserve identity submissions, document versions and verification results."""
from alembic import op
import sqlalchemy as sa

revision = "010_historial_identidad"
down_revision = "009_tarifas_servicios"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "solicitudes_verificacion_identidad",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("usuario_id", "version", name="uq_solicitud_identidad_version"),
        sa.CheckConstraint("version > 0", name="ck_solicitud_identidad_version"),
    )
    op.create_index("ix_solicitudes_verificacion_identidad_usuario_id", "solicitudes_verificacion_identidad", ["usuario_id"])
    op.create_table(
        "resultados_verificacion_identidad",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("solicitud_id", sa.Uuid(), sa.ForeignKey("solicitudes_verificacion_identidad.id"), nullable=False),
        sa.Column("estado", sa.String(30), nullable=False),
        sa.Column("motivo", sa.Text(), nullable=False),
        sa.Column("proceso_responsable", sa.String(100), nullable=False),
        sa.Column("version_proceso", sa.String(50), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("estado IN ('PENDIENTE', 'EN_REVISION', 'RECHAZADO', 'VERIFICADO', 'INCIERTO', 'ERROR')", name="ck_resultado_identidad_estado"),
    )
    op.create_index("ix_resultados_verificacion_identidad_solicitud_id", "resultados_verificacion_identidad", ["solicitud_id"])
    op.add_column("documentos_identidad", sa.Column("solicitud_id", sa.Uuid(), nullable=True))

    # Existing documents are imported as version 1. No deleted history is invented.
    # Deterministic UUIDs avoid requiring a PostgreSQL extension.
    op.execute(sa.text("""
        INSERT INTO solicitudes_verificacion_identidad (id, usuario_id, version, creado_en)
        SELECT md5('identity-import:' || usuario_id::text)::uuid, usuario_id, 1, min(creado_en)
        FROM documentos_identidad GROUP BY usuario_id
    """))
    op.execute(sa.text("""
        UPDATE documentos_identidad
        SET solicitud_id = md5('identity-import:' || usuario_id::text)::uuid
    """))
    op.execute(sa.text("""
        INSERT INTO resultados_verificacion_identidad
            (id, solicitud_id, estado, motivo, proceso_responsable, version_proceso)
        SELECT md5('identity-import-result:' || s.usuario_id::text)::uuid, s.id,
            CASE WHEN u.estado_identidad IN ('PENDIENTE', 'EN_REVISION', 'RECHAZADO', 'VERIFICADO', 'INCIERTO', 'ERROR')
                THEN u.estado_identidad ELSE 'PENDIENTE' END,
            'Estado observado al importar documentos existentes; no es una nueva evaluación. La fecha corresponde a la importación.',
            'MIGRACION_DOCUMENTOS_EXISTENTES', '010'
        FROM solicitudes_verificacion_identidad s JOIN usuarios u ON u.id = s.usuario_id
    """))
    op.alter_column("documentos_identidad", "solicitud_id", nullable=False)
    op.create_foreign_key("fk_documento_solicitud", "documentos_identidad", "solicitudes_verificacion_identidad", ["solicitud_id"], ["id"])
    op.create_index("ix_documentos_identidad_solicitud_id", "documentos_identidad", ["solicitud_id"])
    op.create_unique_constraint("uq_documento_solicitud_tipo", "documentos_identidad", ["solicitud_id", "tipo"])
    for table in ("solicitudes_verificacion_identidad", "resultados_verificacion_identidad"):
        op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'REVOKE ALL ON TABLE "{table}" FROM anon, authenticated'))


def downgrade():
    # Dropping versions after multiple submissions would silently destroy audit
    # history and leave the old API unable to distinguish the current documents.
    raise RuntimeError("La reversión requiere exportar y preservar el historial; no hay downgrade automático.")
