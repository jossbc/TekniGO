from datetime import date, timedelta
import importlib
import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import Base
from app.models import (
    IdentityDocument,
    IdentityVerificationRequest,
    TechnicianProfile,
    TechnicianStatusAudit,
    User,
    UserRole,
)


class TechnicianActivationSchemaTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)

    def _create_technician(self, db: Session) -> tuple[User, TechnicianProfile]:
        user = User(
            firebase_uid="tech-uid-1",
            nombre_completo="Carlos Tecnico",
            correo="carlos@example.com",
            dni="0801199512345",
            telefono="+50499887766",
        )
        db.add(user)
        db.flush()
        db.add(UserRole(usuario_id=user.id, rol="TECNICO"))
        profile = TechnicianProfile(usuario_id=user.id)
        db.add(profile)
        db.commit()
        db.refresh(user)
        db.refresh(profile)
        return user, profile

    def test_profile_and_user_defaults_separate_dimensions(self):
        with Session(self.engine) as db:
            user, profile = self._create_technician(db)
            self.assertFalse(user.telefono_verificado)
            self.assertFalse(profile.disponible)
            self.assertEqual(profile.estado_verificacion, "NO_VERIFICADO")
            self.assertFalse(profile.suspendido)
            self.assertFalse(profile.restriccion_financiera)
            self.assertEqual(profile.estado_certificacion, "NO_CERTIFICADO")
            self.assertIsNone(profile.calificacion_promedio)
            self.assertIsNone(profile.verificado_en)
            self.assertIsNone(profile.motivo_restriccion)

    def test_profile_verification_state_constraint(self):
        with Session(self.engine) as db:
            _, profile = self._create_technician(db)
            for valid_state in (
                "NO_VERIFICADO",
                "PENDIENTE",
                "EN_REVISION",
                "VERIFICADO",
                "RECHAZADO",
                "RESTRINGIDO",
            ):
                profile.estado_verificacion = valid_state
                db.commit()

            profile.estado_verificacion = "ESTADO_INVALIDO"
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()

    def test_document_expiration_and_extended_states(self):
        with Session(self.engine) as db:
            user, _ = self._create_technician(db)
            req = IdentityVerificationRequest(usuario_id=user.id, version=1)
            db.add(req)
            db.flush()

            emision = date.today()
            vencimiento = emision + timedelta(days=90)
            doc = IdentityDocument(
                usuario_id=user.id,
                solicitud_id=req.id,
                tipo="ANTECEDENTES_PENALES",
                ruta_privada="identidad/test-penales.enc",
                content_type="application/pdf",
                tamano_bytes=1024,
                sha256="a" * 64,
                estado="APROBADO",
                fecha_emision_declarada=emision,
                fecha_vencimiento=vencimiento,
            )
            db.add(doc)
            db.commit()
            self.assertEqual(doc.fecha_vencimiento, vencimiento)

            for state in ("VENCIDO", "INVALIDADO"):
                doc.estado = state
                db.commit()

            doc.estado = "DESCONOCIDO"
            with self.assertRaises(IntegrityError):
                db.commit()
            db.rollback()

    def test_status_audit_records_transition_metadata(self):
        with Session(self.engine) as db:
            user, profile = self._create_technician(db)
            audit = TechnicianStatusAudit(
                usuario_id=profile.usuario_id,
                estado_anterior="EN_REVISION",
                estado_nuevo="VERIFICADO",
                habilitado_anterior=False,
                habilitado_nuevo=True,
                causa="DNI, selfie, antecedentes policiales/penales vigentes y teléfono validado.",
                actor_usuario_id=user.id,
                proceso_responsable="REVISION_DOCUMENTAL_ADMIN",
            )
            db.add(audit)
            db.commit()

            saved = db.scalar(
                select(TechnicianStatusAudit).where(TechnicianStatusAudit.usuario_id == user.id)
            )
            self.assertIsNotNone(saved)
            self.assertEqual(saved.estado_anterior, "EN_REVISION")
            self.assertEqual(saved.estado_nuevo, "VERIFICADO")
            self.assertFalse(saved.habilitado_anterior)
            self.assertTrue(saved.habilitado_nuevo)
            self.assertEqual(saved.proceso_responsable, "REVISION_DOCUMENTAL_ADMIN")
            self.assertIsNotNone(saved.creado_en)

    def test_migration_013_prevents_destructive_downgrade(self):
        migration = importlib.import_module("migrations.versions.013_vigencias_y_auditoria_tecnicos")
        self.assertEqual(migration.revision, "013_vigencias_y_auditoria_tecnicos")
        self.assertEqual(migration.down_revision, "012_documentos_tecnico")
        with self.assertRaises(RuntimeError):
            migration.downgrade()


if __name__ == "__main__":
    unittest.main()

