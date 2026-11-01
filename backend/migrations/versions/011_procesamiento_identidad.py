"""Private audit evidence for identity processing (HU-17A ST-04)."""
from alembic import op
import sqlalchemy as sa

revision = "011_procesamiento_identidad"
down_revision = "010_historial_identidad"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('resultados_verificacion_identidad', sa.Column('evidencia', sa.JSON(), nullable=True))


def downgrade():
    raise RuntimeError('La reversión requiere preservar la evidencia privada de las evaluaciones.')
