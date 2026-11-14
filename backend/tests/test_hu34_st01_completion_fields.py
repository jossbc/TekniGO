from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib
import unittest
from unittest.mock import MagicMock, patch
import uuid

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.client_requests import get_client_request_detail
from app.database import Base
from app.models import (
    CONFIRMATION_WINDOW_HOURS,
    DirectRequest,
    OpenRequest,
    Service,
    Specialty,
    TechnicianProfile,
    User,
    UserRole,
    calculate_confirmation_expiration,
    ensure_utc_datetime,
    format_tegucigalpa_iso,
)
from app.open_requests import get_open_request_for_viewer


class Hu34St01CompletionFieldsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        self.client = User(
            firebase_uid="client-hu34-1",
            correo="cliente.hu34@example.com",
            nombre_completo="Valeria Cliente",
            dni="0801200011111",
            telefono="+50491112222",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.tech = User(
            firebase_uid="tech-hu34-1",
            correo="tecnico.hu34@example.com",
            nombre_completo="Ricardo Técnico",
            dni="0801199522222",
            telefono="+50493334444",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add_all([self.client, self.tech])
        self.db.flush()
        self.db.add_all(
            [
                UserRole(usuario_id=self.client.id, rol="CLIENTE"),
                UserRole(usuario_id=self.tech.id, rol="TECNICO"),
                TechnicianProfile(
                    usuario_id=self.tech.id,
                    disponible=True,
                    estado_verificacion="VERIFICADO",
                ),
            ]
        )

        self.specialty = Specialty(nombre="Electricidad Residencial")
        self.db.add(self.specialty)
        self.db.flush()

        self.service = Service(
            usuario_id=self.tech.id,
            especialidad_id=self.specialty.id,
            nombre="Mantenimiento de panel eléctrico",
            descripcion="Revisión y ajuste de circuitos",
            tarifa_base=Decimal("650.00"),
            unidad_cobro="POR_SERVICIO",
            activo=True,
        )
        self.db.add(self.service)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _create_direct_request_en_proceso(self) -> DirectRequest:
        now = datetime(2026, 11, 10, 15, 0, 0, tzinfo=timezone.utc)
        req = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.client.firebase_uid,
            tecnico_id=self.tech.firebase_uid,
            servicio_id=str(self.service.id),
            municipio="Distrito Central",
            sector="Col. Palmira",
            direccion_exacta="Av. República de Chile, casa #18",
            fecha_hora=now + timedelta(days=1),
            descripcion="Revisión de panel principal",
            cantidad=Decimal("1.00"),
            tarifa_aplicada=Decimal("650.00"),
            unidad_cobro="POR_SERVICIO",
            monto_base=Decimal("650.00"),
            metodo_pago="tarjeta",
            estado="EN_PROCESO",
            creada_en=now,
        )
        self.db.add(req)
        self.db.commit()
        self.db.refresh(req)
        return req

    def _create_open_request_en_proceso(self) -> OpenRequest:
        now = datetime(2026, 11, 10, 15, 0, 0, tzinfo=timezone.utc)
        req = OpenRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.client.firebase_uid,
            tecnico_id=self.tech.firebase_uid,
            especialidad_id=str(self.specialty.id),
            servicio_id=str(self.service.id),
            modalidad="ABIERTA",
            municipio="Distrito Central",
            sector="Col. Palmira",
            direccion_exacta="Av. República de Chile, casa #18",
            fecha_hora=now + timedelta(days=1),
            descripcion="Instalación de circuito dedicado",
            presupuesto=Decimal("900.00"),
            monto_base=Decimal("900.00"),
            metodo_pago="tarjeta",
            estado="En proceso",
            creada_en=now,
        )
        self.db.add(req)
        self.db.commit()
        self.db.refresh(req)
        return req

    def test_schema_columns_and_indexes_exist_in_both_request_tables(self):
        inspector = inspect(self.engine)
        for table_name, expected_index in (
            ("solicitudes_directas", "ix_solicitudes_directas_vencimiento_confirmacion"),
            ("solicitudes_abiertas", "ix_solicitudes_abiertas_vencimiento_confirmacion"),
        ):
            columns = {col["name"]: col for col in inspector.get_columns(table_name)}
            self.assertIn("finalizacion_reportada_en", columns)
            self.assertIn("vencimiento_confirmacion_en", columns)
            self.assertIn("pendiente_recepcion", columns)
            self.assertTrue(columns["finalizacion_reportada_en"]["nullable"])
            self.assertTrue(columns["vencimiento_confirmacion_en"]["nullable"])
            self.assertFalse(columns["pendiente_recepcion"]["nullable"])

            index_names = {idx["name"] for idx in inspector.get_indexes(table_name)}
            self.assertIn(expected_index, index_names)

    def test_defaults_and_controlled_clock_48h_deadline_with_tegucigalpa_timezone(self):
        direct_req = self._create_direct_request_en_proceso()
        open_req = self._create_open_request_en_proceso()

        for record in (direct_req, open_req):
            self.assertIsNone(record.finalizacion_reportada_en)
            self.assertIsNone(record.vencimiento_confirmacion_en)
            self.assertFalse(record.pendiente_recepcion)

        # Reloj controlado: 2026-11-12 20:30:00 UTC (14:30:00 en America/Tegucigalpa -06:00)
        controlled_now = datetime(2026, 11, 12, 20, 30, 0, tzinfo=timezone.utc)
        expected_expiration = datetime(2026, 11, 14, 20, 30, 0, tzinfo=timezone.utc)
        self.assertEqual(CONFIRMATION_WINDOW_HOURS, 48)
        self.assertEqual(calculate_confirmation_expiration(controlled_now), expected_expiration)

        first_registered = direct_req.registrar_finalizacion_reportada(now=controlled_now)
        self.assertTrue(first_registered)
        self.db.commit()
        self.db.refresh(direct_req)

        self.assertEqual(ensure_utc_datetime(direct_req.finalizacion_reportada_en), controlled_now)
        self.assertEqual(ensure_utc_datetime(direct_req.vencimiento_confirmacion_en), expected_expiration)
        self.assertTrue(direct_req.pendiente_recepcion)
        self.assertEqual(direct_req.estado, "EN_PROCESO")
        self.assertEqual(direct_req.finalizacion_reportada_local, "2026-11-12T14:30:00-06:00")
        self.assertEqual(direct_req.vencimiento_confirmacion_local, "2026-11-14T14:30:00-06:00")

        # CA-04: Reportar de nuevo con reloj posterior no reinicia el plazo
        later_clock = controlled_now + timedelta(hours=10)
        second_registered = direct_req.registrar_finalizacion_reportada(now=later_clock)
        self.assertFalse(second_registered)
        self.assertEqual(ensure_utc_datetime(direct_req.finalizacion_reportada_en), controlled_now)
        self.assertEqual(ensure_utc_datetime(direct_req.vencimiento_confirmacion_en), expected_expiration)

    def test_serialization_exposes_completion_fields_to_client_and_technician(self):
        controlled_now = datetime(2026, 11, 12, 18, 0, 0, tzinfo=timezone.utc)
        direct_req = self._create_direct_request_en_proceso()
        direct_req.registrar_finalizacion_reportada(now=controlled_now)

        open_req = self._create_open_request_en_proceso()
        open_req.registrar_finalizacion_reportada(now=controlled_now)
        self.db.commit()

        client_direct_detail = get_client_request_detail(
            direct_req.id,
            {"uid": self.client.firebase_uid},
            self.db,
        )
        self.assertEqual(client_direct_detail["estado"], "EN_PROCESO")
        self.assertTrue(client_direct_detail["pendiente_recepcion"])
        self.assertEqual(client_direct_detail["finalizacion_reportada_en"], "2026-11-12T18:00:00+00:00")
        self.assertEqual(client_direct_detail["vencimiento_confirmacion_en"], "2026-11-14T18:00:00+00:00")
        self.assertEqual(client_direct_detail["finalizacion_reportada_local"], "2026-11-12T12:00:00-06:00")
        self.assertEqual(client_direct_detail["vencimiento_confirmacion_local"], "2026-11-14T12:00:00-06:00")

        tech_open_detail = get_open_request_for_viewer(
            open_req.id,
            {"uid": self.tech.firebase_uid},
            self.db,
        )
        self.assertEqual(tech_open_detail["estado"], "EN PROCESO")
        self.assertTrue(tech_open_detail["pendiente_recepcion"])
        self.assertEqual(tech_open_detail["finalizacion_reportada_en"], "2026-11-12T18:00:00+00:00")
        self.assertEqual(tech_open_detail["vencimiento_confirmacion_en"], "2026-11-14T18:00:00+00:00")
        self.assertEqual(tech_open_detail["vencimiento_confirmacion_local"], "2026-11-14T12:00:00-06:00")
        self.assertEqual(format_tegucigalpa_iso(None), None)

    def test_migration_020_revision_chain_and_operations(self):
        migration = importlib.import_module("migrations.versions.020_finalizacion_confirmacion")
        self.assertEqual(migration.revision, "020_finalizacion_confirmacion")
        self.assertEqual(migration.down_revision, "019_certificaciones_oficio")
        self.assertLessEqual(len(migration.revision), 32)

        with patch.object(migration, "op") as mock_op:
            batch_ctx = MagicMock()
            mock_op.batch_alter_table.return_value.__enter__.return_value = batch_ctx
            migration.upgrade()
            self.assertEqual(mock_op.batch_alter_table.call_count, 2)
            self.assertEqual(batch_ctx.add_column.call_count, 6)
            self.assertEqual(mock_op.create_index.call_count, 2)

            mock_op.reset_mock()
            batch_ctx.reset_mock()
            migration.downgrade()
            self.assertEqual(mock_op.drop_index.call_count, 2)
            self.assertEqual(mock_op.batch_alter_table.call_count, 2)
            self.assertEqual(batch_ctx.drop_column.call_count, 6)


if __name__ == "__main__":
    unittest.main()
