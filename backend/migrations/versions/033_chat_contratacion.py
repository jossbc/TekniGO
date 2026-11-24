"""HU-48 ST-01: mensajes de chat operativo por contratación."""
import sqlalchemy as sa
from alembic import op

revision = "033_chat_contratacion"
down_revision = "032_desembolsos_tecnico"
branch_labels = None
depends_on = None

_WITH_CLIENT_ID = sa.text("client_message_id IS NOT NULL")


def upgrade():
    op.create_table(
        "mensajes_chat_contratacion",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("modalidad", sa.String(length=10), nullable=False),
        sa.Column("solicitud_id", sa.String(length=36), nullable=False),
        sa.Column("autor_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("autor_uid", sa.String(length=128), nullable=False),
        sa.Column("rol_autor", sa.String(length=20), nullable=False),
        sa.Column("contenido", sa.Text(), nullable=False),
        sa.Column("client_message_id", sa.String(length=64), nullable=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("modalidad IN ('DIRECTA', 'ABIERTA')", name="ck_chat_mensaje_modalidad"),
        sa.CheckConstraint("rol_autor IN ('CLIENTE', 'TECNICO')", name="ck_chat_mensaje_rol_autor"),
        sa.CheckConstraint("length(trim(solicitud_id)) > 0", name="ck_chat_mensaje_solicitud"),
        sa.CheckConstraint("length(trim(contenido)) > 0", name="ck_chat_mensaje_contenido"),
        sa.CheckConstraint(
            "client_message_id IS NULL OR length(trim(client_message_id)) > 0",
            name="ck_chat_mensaje_client_id",
        ),
    )
    op.create_index(
        "ix_chat_mensajes_conversacion_orden",
        "mensajes_chat_contratacion",
        ["modalidad", "solicitud_id", "creado_en", "id"],
    )
    op.create_index("ix_chat_mensajes_autor", "mensajes_chat_contratacion", ["autor_id"])
    op.create_index(
        "uq_chat_mensaje_cliente_idempotencia",
        "mensajes_chat_contratacion",
        ["modalidad", "solicitud_id", "autor_id", "client_message_id"],
        unique=True,
        postgresql_where=_WITH_CLIENT_ID,
        sqlite_where=_WITH_CLIENT_ID,
    )


def downgrade():
    op.drop_index("uq_chat_mensaje_cliente_idempotencia", table_name="mensajes_chat_contratacion")
    op.drop_index("ix_chat_mensajes_autor", table_name="mensajes_chat_contratacion")
    op.drop_index("ix_chat_mensajes_conversacion_orden", table_name="mensajes_chat_contratacion")
    op.drop_table("mensajes_chat_contratacion")
