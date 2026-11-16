"""Add category availability, suggested tariffs and audited operating settings."""
from alembic import op
import sqlalchemy as sa

revision = "023_configuracion_catalogo"
down_revision = "022_historial_solicitud"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("especialidades", sa.Column("activa", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.execute(
        "CREATE UNIQUE INDEX ux_especialidades_nombre_normalizado ON especialidades "
        "(lower(trim(regexp_replace(nombre, '[[:space:]]+', ' ', 'g'))))"
    )
    op.create_table(
        "tarifas_sugeridas",
        sa.Column("especialidad_id", sa.Uuid(), sa.ForeignKey("especialidades.id"), primary_key=True),
        sa.Column("unidad", sa.String(20), primary_key=True),
        sa.Column("promedio", sa.Numeric(10, 2), nullable=False),
        sa.CheckConstraint("promedio > 0", name="ck_tarifa_sugerida_positiva"),
    )
    settings = op.create_table(
        "configuracion_operativa",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("comision_porcentaje", sa.Numeric(5, 2)),
        sa.Column("limite_deuda", sa.Numeric(10, 2)),
        sa.Column("minimo_retiro", sa.Numeric(10, 2)),
        sa.Column("limite_sin_liquidar", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("valores_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("id = 1", name="ck_configuracion_unica"),
        sa.CheckConstraint("comision_porcentaje BETWEEN 0 AND 100", name="ck_configuracion_comision"),
        sa.CheckConstraint("limite_deuda > 0 AND minimo_retiro > 0", name="ck_configuracion_umbrales"),
        sa.CheckConstraint("limite_sin_liquidar = 5", name="ck_configuracion_liquidaciones"),
    )
    op.bulk_insert(settings, [{"id": 1}])
    op.create_table(
        "historial_configuracion_catalogo",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recurso", sa.String(90), nullable=False),
        sa.Column("anterior", sa.JSON()),
        sa.Column("nuevo", sa.JSON(), nullable=False),
        sa.Column("administrador_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), nullable=False),
        sa.Column("fecha", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for table in ("tarifas_sugeridas", "configuracion_operativa", "historial_configuracion_catalogo"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON TABLE {table} FROM anon, authenticated")


def downgrade():
    op.drop_table("historial_configuracion_catalogo")
    op.drop_table("configuracion_operativa")
    op.drop_table("tarifas_sugeridas")
    op.drop_index("ux_especialidades_nombre_normalizado", table_name="especialidades")
    op.drop_column("especialidades", "activa")
