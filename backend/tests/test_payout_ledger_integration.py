"""HU-43 ST-05: débito único o liberación de reserva en libro financiero con saldos reales."""
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import payouts as payout_rules
from app.database import Base
from app.identity_documents import UploadedPart
from app.models import (
    DirectRequest,
    OperationalSettings,
    PayoutLedgerEntry,
    PayoutOrder,
    TechnicianProfile,
    User,
    UserRole,
)
from app.payouts_router import (
    BankAccountInput,
    PayoutDecisionInput,
    PayoutRequestInput,
    decide_payout,
    get_my_payout_summary,
    list_payout_audit,
    request_payout,
    save_my_bank_account,
)
from app.settlements_router import (
    SettlementDecisionSchema,
    decide_settlement,
    submit_settlement,
)
from app.storage import PrivateDocumentStorage

PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"


class PayoutLedgerIntegrationTests(unittest.TestCase):
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

        self.tech = self._add_user("tech-1", "TECNICO", 1)
        self.client_user = self._add_user("cli-1", "CLIENTE", 2)
        self.admin = self._add_user("adm-1", "ADMIN", 3)
        self.db.add(
            TechnicianProfile(
                usuario_id=self.tech.id,
                disponible=True,
                estado_verificacion="VERIFICADO",
                restriccion_financiera=False,
                servicios_efectivo_sin_liquidar=0,
            )
        )
        self.db.add(
            OperationalSettings(
                id=1,
                comision_porcentaje=Decimal("10.00"),
                limite_deuda=Decimal("500.00"),
                minimo_retiro=Decimal("200.00"),
            )
        )
        # Servicio real cerrado con pago digital: acredita L 540.75 netos al técnico
        self._add_closed_service(metodo_pago="tarjeta", digital_credit=540.75, cash_debt=0.0)
        self.db.commit()

        self.tech_claims = {"uid": self.tech.firebase_uid}
        self.admin_claims = {"uid": self.admin.firebase_uid}
        save_my_bank_account(
            BankAccountInput(
                banco="Ficohsa",
                tipo_cuenta="AHORRO",
                titular_nombre="Técnico Real",
                numero_cuenta="2000-4455-8899",
            ),
            self.tech_claims,
            self.db,
        )

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

    def _add_closed_service(self, *, metodo_pago: str, digital_credit: float, cash_debt: float) -> None:
        self.db.add(
            DirectRequest(
                id=str(uuid.uuid4()),
                cliente_id=self.client_user.firebase_uid,
                tecnico_id=self.tech.firebase_uid,
                servicio_id=str(uuid.uuid4()),
                municipio="Tegucigalpa",
                sector="Miraflores",
                direccion_exacta="Bloque A",
                fecha_hora=datetime(2026, 11, 20, 15, 0, tzinfo=timezone.utc),
                descripcion="Instalación eléctrica",
                cantidad=Decimal("1.00"),
                tarifa_aplicada=Decimal("600.83"),
                unidad_cobro="servicio",
                monto_base=Decimal("600.83"),
                monto_total=Decimal("600.83"),
                metodo_pago=metodo_pago,
                estado="COMPLETADA",
                seguimiento_recepcion={
                    "completada": True,
                    "efecto_financiero": {
                        "credito_digital_acreditado": digital_credit,
                        "deuda_comision_efectivo": cash_debt,
                    },
                },
            )
        )

    def test_confirm_records_single_debit_ledger_entry_and_db_prevents_duplicate(self):
        created = request_payout(PayoutRequestInput(importe="240.25"), self.tech_claims, self.db)
        oid = uuid.UUID(created["orden"]["id"])

        # Al solicitar: existe RESERVA_DESEMBOLSO, contable sigue en 540.75, libre bajó a 300.50
        self.assertEqual(created["movimiento_libro"]["tipo_movimiento"], payout_rules.LEDGER_RESERVA)
        self.assertEqual(created["saldos"]["saldo_contable"], "540.75")
        self.assertEqual(created["saldos"]["saldo_reservado"], "240.25")
        self.assertEqual(created["saldos"]["saldo_libre"], "300.50")

        # Al confirmar: se registra DEBITO_DESEMBOLSO único, contable baja a 300.50 y reserva pasa a 0.00
        confirmed = decide_payout(
            oid,
            PayoutDecisionInput(decision="CONFIRMAR", referencia_transferencia="TRF-LEDGER-01"),
            self.admin_claims,
            self.db,
        )
        self.assertEqual(confirmed["movimiento_libro"]["tipo_movimiento"], payout_rules.LEDGER_DEBITO)
        self.assertEqual(confirmed["movimiento_libro"]["referencia_transferencia"], "TRF-LEDGER-01")
        self.assertEqual(confirmed["saldos"]["saldo_contable"], "300.50")
        self.assertEqual(confirmed["saldos"]["saldo_reservado"], "0.00")
        self.assertEqual(confirmed["saldos"]["saldo_libre"], "300.50")

        # Consulta de auditoría + libro devuelve exactamente 2 movimientos (RESERVA + DEBITO)
        audit = list_payout_audit(oid, self.admin_claims, self.db)
        self.assertEqual(
            [m["tipo_movimiento"] for m in audit["movimientos_libro"]],
            [payout_rules.LEDGER_RESERVA, payout_rules.LEDGER_DEBITO],
        )

        # Garantía a nivel de BD: reintentar insertar el mismo débito choca por clave_idempotencia UNIQUE
        order = self.db.get(PayoutOrder, oid)
        with self.assertRaises(IntegrityError):
            payout_rules.record_payout_ledger_entry(
                self.db,
                order=order,
                movement_type=payout_rules.LEDGER_DEBITO,
                saldo_contable=Decimal("60.25"),
                saldo_reservado=Decimal("0.00"),
                saldo_libre=Decimal("60.25"),
                responsable_id=self.admin.id,
                referencia_transferencia="TRF-LEDGER-01",
            )
            self.db.flush()
        self.db.rollback()

        summary = get_my_payout_summary(self.tech_claims, self.db)
        self.assertEqual(summary["saldo_contable"], "300.50")
        self.assertEqual(summary["saldo_libre"], "300.50")

    def test_reject_records_release_entry_without_debit_and_restores_available_balance(self):
        created = request_payout(PayoutRequestInput(importe="400.00"), self.tech_claims, self.db)
        oid = uuid.UUID(created["orden"]["id"])
        self.assertEqual(get_my_payout_summary(self.tech_claims, self.db)["saldo_libre"], "140.75")

        rejected = decide_payout(
            oid,
            PayoutDecisionInput(decision="RECHAZAR", motivo_rechazo="Titular no coincide en banco"),
            self.admin_claims,
            self.db,
        )
        self.assertEqual(rejected["movimiento_libro"]["tipo_movimiento"], payout_rules.LEDGER_LIBERACION)
        self.assertEqual(rejected["saldos"]["saldo_contable"], "540.75")
        self.assertEqual(rejected["saldos"]["saldo_reservado"], "0.00")
        self.assertEqual(rejected["saldos"]["saldo_libre"], "540.75")

        # No existe ningún débito en el libro
        debit_count = self.db.scalar(
            select(func.count()).select_from(PayoutLedgerEntry).where(
                PayoutLedgerEntry.tipo_movimiento == payout_rules.LEDGER_DEBITO
            )
        )
        self.assertEqual(debit_count, 0)

        # El técnico puede volver a solicitar usando todo el saldo liberado
        retry = request_payout(PayoutRequestInput(importe="540.75"), self.tech_claims, self.db)
        self.assertEqual(retry["saldos"]["saldo_libre"], "0.00")

    def test_hu42_and_hu43_end_to_end_debt_blocks_payout_until_settled(self):
        # Se agrega deuda real por servicio en efectivo (L 80.00)
        self._add_closed_service(metodo_pago="efectivo", digital_credit=0.0, cash_debt=80.0)
        self.db.commit()

        # Mientras tenga deuda > 0, no puede retirar ganancias
        with self.assertRaises(HTTPException) as blocked:
            request_payout(PayoutRequestInput(importe="200.00"), self.tech_claims, self.db)
        self.assertEqual(blocked.exception.status_code, 409)

        # Liquida su deuda en HU-42 y admin aprueba totalmente
        settlement = submit_settlement(
            importe="80.00",
            referencia="BAC-SETTLE-80",
            comprobante=UploadedPart(PNG, "image/png"),
            claims=self.tech_claims,
            db=self.db,
            storage=self.storage,
        )
        decide_settlement(
            uuid.UUID(settlement["id"]),
            SettlementDecisionSchema(decision="APROBAR", importe_verificado="80.00"),
            self.admin_claims,
            self.db,
        )

        # Con deuda en 0.00 ya puede solicitar desembolso en HU-43 y administración lo confirma
        payout = request_payout(PayoutRequestInput(importe="300.00"), self.tech_claims, self.db)
        oid = uuid.UUID(payout["orden"]["id"])
        confirmed = decide_payout(
            oid,
            PayoutDecisionInput(decision="CONFIRMAR", referencia_transferencia="TRF-E2E-300"),
            self.admin_claims,
            self.db,
        )
        self.assertEqual(confirmed["orden"]["estado"], "PAGADA")
        self.assertEqual(confirmed["saldos"]["saldo_contable"], "240.75")
        self.assertEqual(confirmed["saldos"]["saldo_libre"], "240.75")


if __name__ == "__main__":
    unittest.main()
