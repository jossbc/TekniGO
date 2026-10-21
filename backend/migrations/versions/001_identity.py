"""Identity, multiple roles and technician profile for HU-01 to HU-04."""
from alembic import op
import sqlalchemy as sa

revision = "001_identity"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("usuarios",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("firebase_uid", sa.String(128), nullable=False, unique=True),
        sa.Column("nombre_completo", sa.String(150), nullable=False),
        sa.Column("correo", sa.String(254), nullable=False),
        sa.Column("dni", sa.String(13), nullable=False, unique=True),
        sa.Column("telefono", sa.String(16), nullable=False, unique=True),
        sa.Column("estado_identidad", sa.String(30), nullable=False),
        sa.Column("creado_en", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_table("usuarios_roles",
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), primary_key=True),
        sa.Column("rol", sa.String(20), primary_key=True),
        sa.CheckConstraint("rol IN ('CLIENTE', 'TECNICO', 'ADMIN')", name="ck_usuario_rol"))
    op.create_table("perfiles_tecnicos",
        sa.Column("usuario_id", sa.Uuid(), sa.ForeignKey("usuarios.id"), primary_key=True),
        sa.Column("disponible", sa.Boolean(), nullable=False),
        sa.Column("estado_verificacion", sa.String(30), nullable=False))
    # FastAPI is the sole business-data entry point. No public Data API access.
    for table in ("usuarios", "usuarios_roles", "perfiles_tecnicos"):
        op.execute(sa.text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
        op.execute(sa.text(f'REVOKE ALL ON TABLE "{table}" FROM anon, authenticated'))


def downgrade():
    op.drop_table("perfiles_tecnicos")
    op.drop_table("usuarios_roles")
    op.drop_table("usuarios")
