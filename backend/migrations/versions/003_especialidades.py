"""Catalog of specialties for HU-07."""
import uuid
from alembic import op
import sqlalchemy as sa

"""Catalog of specialties for HU-07."""
import uuid
from alembic import op
import sqlalchemy as sa

revision = "002_especialidades"
down_revision = "044b490a5a5d"
branch_labels = None
depends_on = None

OFICIOS = [
    "Fontanería",
    "Electricidad",
    "Cerrajería",
    "Pintura",
    "Carpintería",
    "Reparación de equipos",
]


def upgrade():
    especialidades = op.create_table("especialidades",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("nombre", sa.String(80), nullable=False, unique=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.bulk_insert(especialidades, [{"id": uuid.uuid4(), "nombre": nombre} for nombre in OFICIOS])
    # FastAPI is the sole business-data entry point. No public Data API access.
    op.execute(sa.text('ALTER TABLE "especialidades" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text('REVOKE ALL ON TABLE "especialidades" FROM anon, authenticated'))


def downgrade():
    op.drop_table("especialidades")
down_revision = "044b490a5a5d"
branch_labels = None
depends_on = None

OFICIOS = [
    "Fontanería",
    "Electricidad",
    "Cerrajería",
    "Pintura",
    "Carpintería",
    "Reparación de equipos",
]


def upgrade():
    especialidades = op.create_table("especialidades",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("nombre", sa.String(80), nullable=False, unique=True),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.bulk_insert(especialidades, [{"id": uuid.uuid4(), "nombre": nombre} for nombre in OFICIOS])
    # FastAPI is the sole business-data entry point. No public Data API access.
    op.execute(sa.text('ALTER TABLE "especialidades" ENABLE ROW LEVEL SECURITY'))
    op.execute(sa.text('REVOKE ALL ON TABLE "especialidades" FROM anon, authenticated'))


def downgrade():
    op.drop_table("especialidades")