"""015_solicitudes_directas"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'de1f7a2e9dd6'
down_revision = '014_decisiones_documentales'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('solicitudes_directas',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('cliente_id', sa.String(), nullable=False),
    sa.Column('tecnico_id', sa.String(), nullable=False),
    sa.Column('servicio_id', sa.Uuid(), nullable=False),
    sa.Column('municipio', sa.String(), nullable=False),
    sa.Column('sector', sa.String(), nullable=False),
    sa.Column('direccion_exacta', sa.String(), nullable=False),
    sa.Column('fecha_hora', sa.DateTime(timezone=True), nullable=False),
    sa.Column('descripcion', sa.Text(), nullable=False),
    sa.Column('cantidad', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('tarifa_aplicada', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('unidad_cobro', sa.String(), nullable=False),
    sa.Column('monto_base', sa.Numeric(precision=10, scale=2), nullable=False),
    sa.Column('metodo_pago', sa.String(), nullable=False),
    sa.Column('estado', sa.String(), nullable=False),
    sa.Column('creada_en', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['cliente_id'], ['usuarios.firebase_uid'], ),
    sa.ForeignKeyConstraint(['servicio_id'], ['servicios.id'], ),
    sa.ForeignKeyConstraint(['tecnico_id'], ['usuarios.firebase_uid'], ),
    sa.PrimaryKeyConstraint('id')
    )

def downgrade():
    op.drop_table('solicitudes_directas')