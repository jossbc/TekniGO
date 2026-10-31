"""Agregar antecedentes y vigencia para verificación de técnicos (HU-16 ST-02)."""
from alembic import op
import sqlalchemy as sa

revision = "010_documentos_tecnico"
down_revision = "009_tarifas_servicios"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("documentos_identidad") as batch_op:
        batch_op.drop_constraint("ck_documento_identidad_tipo", type_="check")
        batch_op.create_check_constraint(
            "ck_documento_identidad_tipo",
            "tipo IN ('DNI_FRONTAL', 'DNI_REVERSO', 'SELFIE', 'ANTECEDENTES_POLICIALES', 'ANTECEDENTES_PENALES')",
        )
        batch_op.alter_column("tipo", type_=sa.String(length=40))
        batch_op.add_column(sa.Column("fecha_emision_declarada", sa.Date(), nullable=True))

    with op.batch_alter_table("perfiles_tecnicos") as batch_op:
        batch_op.add_column(sa.Column("observaciones_verificacion", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("perfiles_tecnicos") as batch_op:
        batch_op.drop_column("observaciones_verificacion")

    with op.batch_alter_table("documentos_identidad") as batch_op:
        batch_op.drop_column("fecha_emision_declarada")
        batch_op.alter_column("tipo", type_=sa.String(length=30))
        batch_op.drop_constraint("ck_documento_identidad_tipo", type_="check")
        batch_op.create_check_constraint(
            "ck_documento_identidad_tipo",
            "tipo IN ('DNI_FRONTAL', 'DNI_REVERSO', 'SELFIE')",
        )