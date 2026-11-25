"""020_metodo_pago_desglose"""
from alembic import op
import sqlalchemy as sa

revision = "020_metodo_pago_desglose"
down_revision = "019_certificaciones_oficio"
branch_labels = None
depends_on = None


def upgrade():
    # Columnas para solicitudes directas
    op.add_column("solicitudes_directas", sa.Column("referencia_pasarela", sa.String(length=100), nullable=True))
    op.add_column("solicitudes_directas", sa.Column("monto_ajuste", sa.Numeric(precision=10, scale=2), nullable=False, server_default="0.00"))
    op.add_column("solicitudes_directas", sa.Column("metodo_pago_ajuste", sa.String(length=20), nullable=True))
    op.add_column("solicitudes_directas", sa.Column("referencia_pasarela_ajuste", sa.String(length=100), nullable=True))
    op.add_column("solicitudes_directas", sa.Column("monto_total", sa.Numeric(precision=10, scale=2), nullable=True))
    op.execute("UPDATE solicitudes_directas SET monto_total = monto_base WHERE monto_total IS NULL")

    # Columnas para solicitudes abiertas
    op.add_column("solicitudes_abiertas", sa.Column("referencia_pasarela", sa.String(length=100), nullable=True))
    op.add_column("solicitudes_abiertas", sa.Column("monto_ajuste", sa.Numeric(precision=10, scale=2), nullable=False, server_default="0.00"))
    op.add_column("solicitudes_abiertas", sa.Column("metodo_pago_ajuste", sa.String(length=20), nullable=True))
    op.add_column("solicitudes_abiertas", sa.Column("referencia_pasarela_ajuste", sa.String(length=100), nullable=True))
    op.add_column("solicitudes_abiertas", sa.Column("monto_total", sa.Numeric(precision=10, scale=2), nullable=True))
    op.execute("UPDATE solicitudes_abiertas SET monto_total = monto_base WHERE monto_total IS NULL")


def downgrade():
    op.drop_column("solicitudes_abiertas", "monto_total")
    op.drop_column("solicitudes_abiertas", "referencia_pasarela_ajuste")
    op.drop_column("solicitudes_abiertas", "metodo_pago_ajuste")
    op.drop_column("solicitudes_abiertas", "monto_ajuste")
    op.drop_column("solicitudes_abiertas", "referencia_pasarela")

    op.drop_column("solicitudes_directas", "monto_total")
    op.drop_column("solicitudes_directas", "referencia_pasarela_ajuste")
    op.drop_column("solicitudes_directas", "metodo_pago_ajuste")
    op.drop_column("solicitudes_directas", "monto_ajuste")
    op.drop_column("solicitudes_directas", "referencia_pasarela")