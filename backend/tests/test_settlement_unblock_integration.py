"""HU-42 ST-05: integración de desbloqueo tras regularización total (con deuda real de servicios)."""
import tempfile
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.identity_documents import UploadedPart
from app.models import (
    DirectRequest,
    IdentityDocument,
    IdentityVerificationRequest,
    TechnicianProfile,
    User,
    UserRole,
)
from app.settlements_router import (
    SettlementDecisionSchema,
    decide_settlement,
    get_settlement_summary,
    submit_settlement,
)
from app.storage import PrivateDocumentStorage
from app.technician_activation import (
    REQUIRED_TECHNICIAN_DOCS,
    evaluate_technician_requirements,
    require_operational_technician,
)

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"


class SettlementUnblockIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            "os.environ",
            {
                "PRIVATE_STORAGE_SECRET": "test-only-private-storage-secret-32-bytes",
                "PRIVATE_STORAGE_BACKEND": "local",
                "PRIVATE_STORAGE_DIR": self.tmp.name,
                "SUPABASE_URL": "",
                "SUPABASE_SERVICE_ROLE_KEY": "",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.storage = PrivateDocumentStorage(base_dir=Path(self.tmp.name))

        self.tech_user = self._add_user("tech-1", "TECNICO", 1)
        self.client_user = self._add_user("cli-1", "CLIENTE", 2)
        self.admin_user = self._add_user("adm-1", "ADMIN", 3)

        self.profile = TechnicianProfile(
            usuario_id=self.tech_user.id,
            disponible=True,
            estado_verificacion="VERIFICADO",
            suspendido=False,
            restriccion_financiera=True,
            servicios_efectivo_sin_liquidar=5,
        )
        self.db.add(self.profile)
        self._add_valid_technician_docs(self.tech_user.id)
        # Deuda real persistida por cierre de servicio en efectivo (como float en JSON de service_reception.py)
        self._add_closed_cash_service(commission_debt=150.5)
        self.db.commit()

        self.tech_claims = {"uid": self.tech_user.firebase_uid}
        self.admin_claims = {"uid": self.admin_user.firebase_uid}

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def _add_user(self, uid: str, role: str, n: int) -> User:
        user = User(
            firebase_uid=uid,
            correo=f"{uid}@example.com",
            nombre_completo=f"Usuario {uid}",
            dni=f"08012000000{n:02d}",
            telefono=f"+5049{n:07d}",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add(user)
        self.db.flush()
        self.db.add(UserRole(usuario_id=user.id, rol=role))
        return user

    def _add_valid_technician_docs(self, user_id: uuid.UUID) -> None:
        today = date.today()
        req = IdentityVerificationRequest(id=uuid.uuid4(), usuario_id=user_id, version=1)
        self.db.add(req)
        self.db.flush()
        for doc_type, _ in REQUIRED_TECHNICIAN_DOCS:
            self.db.add(
                IdentityDocument(
                    usuario_id=user_id,
                    solicitud_id=req.id,
                    tipo=doc_type,
                    ruta_privada=f"private://documentos-identidad/identidad/{user_id}/{doc_type.lower()}_{uuid.uuid4().hex}.png.enc",
                    content_type="image/png",
                    tamano_bytes=len(PNG),
                    sha256=uuid.uuid4().hex + uuid.uuid4().hex,
                    estado="APROBADO",
                    fecha_emision_declarada=today,
                    fecha_vencimiento=today + timedelta(days=180),
                )
            )

    def _add_closed_cash_service(self, *, commission_debt: float) -> None:
        self.db.add(
            DirectRequest(
                id=str(uuid.uuid4()),
                cliente_id=self.client_user.firebase_uid,
                tecnico_id=self.tech_user.firebase_uid,
                servicio_id=str(uuid.uuid4()),
                municipio="Tegucigalpa",
                sector="Palmira",
                direccion_exacta="Calle 1",
                fecha_hora=datetime(2026, 11, 20, 14, 0, tzinfo=timezone.utc),
                descripcion="Reparación de tubería",
                cantidad=Decimal("1.00"),
                tarifa_aplicada=Decimal("1505.00"),
                unidad_cobro="servicio",
                monto_base=Decimal("1505.00"),
                monto_total=Decimal("1505.00"),
                metodo_pago="efectivo",
                estado="COMPLETADA",
                seguimiento_recepcion={
                    "completada": True,
                    "efecto_financiero": {
                        "tipo_efecto": "DEUDA_COMISION_EFECTIVO_REGISTRADA",
                        "deuda_comision_efectivo": commission_debt,
                    },
                },
            )
        )

    def test_full_regularization_with_real_debt_unblocks_operational_eligibility(self):
        # 1. Antes de enviar: bloqueado operativamente por restriccion_financiera
        with self.assertRaises(HTTPException) as before_err:
            require_operational_technician(self.tech_user, self.db)
        self.assertEqual(before_err.exception.status_code, 403)
        self.assertIn("financiera", before_err.exception.detail.lower())

        summary_before = get_settlement_summary(self.tech_claims, self.db)
        self.assertEqual(summary_before["deuda_vigente"], "150.50")
        self.assertTrue(summary_before["restriccion_financiera"])
        self.assertFalse(summary_before["habilitado_operativamente"])

        # 2. Envío de liquidación: queda PENDIENTE, NO desbloquea (CA-02)
        created = submit_settlement(
            importe="150.50",
            referencia="BAC-REAL-1",
            comprobante=UploadedPart(PNG, "image/png"),
            claims=self.tech_claims,
            db=self.db,
            storage=self.storage,
        )
        with self.assertRaises(HTTPException):
            require_operational_technician(self.tech_user, self.db)

        # 3. Aprobación total: deuda real baja a 0.00, reinicia contador y desbloquea (CA-04)
        decide_settlement(
            uuid.UUID(created["id"]),
            SettlementDecisionSchema(decision="APROBAR", importe_verificado="150.50"),
            self.admin_claims,
            self.db,
        )

        self.db.expire_all()
        tech = self.db.get(User, self.tech_user.id)
        profile = require_operational_technician(tech, self.db)
        self.assertFalse(profile.restriccion_financiera)
        self.assertEqual(profile.servicios_efectivo_sin_liquidar, 0)

        summary_after = get_settlement_summary(self.tech_claims, self.db)
        self.assertEqual(summary_after["deuda_vigente"], "0.00")
        self.assertFalse(summary_after["restriccion_financiera"])
        self.assertEqual(summary_after["servicios_efectivo_sin_liquidar"], 0)
        self.assertTrue(summary_after["habilitado_operativamente"])

    def test_cumulative_partial_settlements_unblock_only_when_debt_reaches_zero(self):
        # Primera liquidación parcial: L 100.00 de L 150.50 -> sigue bloqueado (CA-05)
        first = submit_settlement(
            importe="100.00",
            referencia="BAC-P1-100",
            comprobante=UploadedPart(PNG, "image/png"),
            claims=self.tech_claims,
            db=self.db,
            storage=self.storage,
        )
        decide_settlement(
            uuid.UUID(first["id"]),
            SettlementDecisionSchema(decision="APROBAR", importe_verificado="100.00"),
            self.admin_claims,
            self.db,
        )
        self.db.expire_all()
        tech = self.db.get(User, self.tech_user.id)
        with self.assertRaises(HTTPException):
            require_operational_technician(tech, self.db)
        self.assertEqual(get_settlement_summary(self.tech_claims, self.db)["deuda_vigente"], "50.50")
        self.assertEqual(self.db.get(TechnicianProfile, self.tech_user.id).servicios_efectivo_sin_liquidar, 5)

        # Segunda liquidación por el saldo restante L 50.50 -> deuda llega a 0.00 y desbloquea (CA-04)
        second = submit_settlement(
            importe="50.50",
            referencia="BAC-P2-5050",
            comprobante=UploadedPart(JPEG, "image/jpeg"),
            claims=self.tech_claims,
            db=self.db,
            storage=self.storage,
        )
        decide_settlement(
            uuid.UUID(second["id"]),
            SettlementDecisionSchema(decision="APROBAR", importe_verificado="50.50"),
            self.admin_claims,
            self.db,
        )
        self.db.expire_all()
        tech = self.db.get(User, self.tech_user.id)
        profile = require_operational_technician(tech, self.db)
        self.assertFalse(profile.restriccion_financiera)
        self.assertEqual(profile.servicios_efectivo_sin_liquidar, 0)
        self.assertEqual(get_settlement_summary(self.tech_claims, self.db)["deuda_vigente"], "0.00")

    def test_total_regularization_does_not_bypass_administrative_suspension(self):
        self.profile.suspendido = True
        self.db.commit()

        created = submit_settlement(
            importe="150.50",
            referencia="BAC-SUSP-1",
            comprobante=UploadedPart(PNG, "image/png"),
            claims=self.tech_claims,
            db=self.db,
            storage=self.storage,
        )
        decide_settlement(
            uuid.UUID(created["id"]),
            SettlementDecisionSchema(decision="APROBAR", importe_verificado="150.50"),
            self.admin_claims,
            self.db,
        )
        self.db.expire_all()
        tech = self.db.get(User, self.tech_user.id)
        profile = self.db.get(TechnicianProfile, self.tech_user.id)
        self.assertFalse(profile.restriccion_financiera)
        self.assertEqual(profile.servicios_efectivo_sin_liquidar, 0)
        self.assertTrue(profile.suspendido)

        evaluation = evaluate_technician_requirements(tech, profile, self.db)
        self.assertFalse(evaluation["habilitado_operativamente"])
        with self.assertRaises(HTTPException) as caught:
            require_operational_technician(tech, self.db)
        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn("suspensión", caught.exception.detail.lower())

    def test_rejection_preserves_debt_counter_and_operational_block(self):
        created = submit_settlement(
            importe="150.50",
            referencia="BAC-REJ-INT",
            comprobante=UploadedPart(PNG, "image/png"),
            claims=self.tech_claims,
            db=self.db,
            storage=self.storage,
        )
        decide_settlement(
            uuid.UUID(created["id"]),
            SettlementDecisionSchema(decision="RECHAZAR", observacion="Monto no aparece en cuenta"),
            self.admin_claims,
            self.db,
        )
        self.db.expire_all()
        tech = self.db.get(User, self.tech_user.id)
        with self.assertRaises(HTTPException):
            require_operational_technician(tech, self.db)
        summary = get_settlement_summary(self.tech_claims, self.db)
        self.assertEqual(summary["deuda_vigente"], "150.50")
        self.assertTrue(summary["restriccion_financiera"])
        self.assertEqual(summary["servicios_efectivo_sin_liquidar"], 5)


if __name__ == "__main__":
    unittest.main()
