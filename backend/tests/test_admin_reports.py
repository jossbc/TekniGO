from datetime import date, datetime, timezone
from decimal import Decimal
import unittest

from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.admin_reports import demand_report, get_demand_report
from app.database import Base
from app.models import DirectRequest, OpenRequest, RequestStateEvent, Service, Specialty, User, UserRole


class AdminDemandReportTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.addCleanup(self.db.close)
        for index, role in enumerate(("ADMIN", "CLIENTE"), 1):
            uid = role.lower()
            user = User(firebase_uid=uid, correo=f"{uid}@example.com", nombre_completo=uid,
                        dni=f"080120000000{index}", telefono=f"+5049876543{index}")
            self.db.add(user)
            self.db.flush()
            self.db.add(UserRole(usuario_id=user.id, rol=role))
        self.trade = Specialty(nombre="Electricidad")
        self.other_trade = Specialty(nombre="Carpintería")
        self.db.add_all([self.trade, self.other_trade])
        self.db.flush()
        self.service = Service(usuario_id=user.id, especialidad_id=self.trade.id, nombre="Instalación",
                               descripcion="Instalación residencial")
        self.db.add(self.service)
        self.db.commit()

    def add_request(self, model, created, state="Pendiente", municipality="Distrito Central", trade=None):
        values = {
            "cliente_id": "cliente", "municipio": municipality, "sector": "Sector privado",
            "direccion_exacta": "Dirección privada", "fecha_hora": created, "descripcion": "Texto privado",
            "monto_base": Decimal("850.00"), "metodo_pago": "efectivo", "estado": state, "creada_en": created,
        }
        if model is DirectRequest:
            values.update(tecnico_id="tecnico", servicio_id=self.service.id, cantidad=1, tarifa_aplicada=850,
                          unidad_cobro="POR_SERVICIO")
        else:
            values.update(especialidad_id=(trade or self.trade).id, presupuesto=850)
        request = model(**values)
        self.db.add(request)
        self.db.flush()
        return request

    def test_report_uses_local_creation_date_and_does_not_duplicate_state_history(self):
        local_monday = datetime(2026, 11, 10, 4, 0, tzinfo=timezone.utc)
        direct = self.add_request(DirectRequest, local_monday, "Cancelada")
        self.db.add_all(
            RequestStateEvent(modalidad="DIRECTA", solicitud_id=direct.id, estado=state, fecha=local_monday)
            for state in ("ACEPTADA", "EN PROCESO", "CANCELADA")
        )
        self.add_request(OpenRequest, local_monday, "Completada", "San Pedro Sula", self.other_trade)
        self.add_request(OpenRequest, datetime(2026, 11, 9, 5, 59, tzinfo=timezone.utc))
        self.add_request(OpenRequest, datetime(2026, 11, 16, 6, 0, tzinfo=timezone.utc))
        self.db.commit()

        result = get_demand_report(self.db, date(2026, 11, 9), date(2026, 11, 15))
        self.assertEqual(result["resumen"], {
            "total": 2, "directas": 1, "abiertas": 1, "contratadas": 2, "completadas": 1,
        })
        self.assertEqual(result["dias"][0]["solicitudes"], 2)
        self.assertEqual(result["dias"][1]["solicitudes"], 0)
        self.assertEqual(result["destacados"]["semana"][0]["nombre"], "Lunes")
        self.assertEqual(len(result["destacados"]["oficios"]), 2)
        self.assertEqual(len(result["destacados"]["municipios"]), 2)
        self.assertNotIn("Dirección privada", str(result))
        self.assertNotIn("Sector privado", str(result))

        filtered = get_demand_report(self.db, date(2026, 11, 9), date(2026, 11, 15),
                                     str(self.other_trade.id), "Cortés", "san pedro sula")
        self.assertEqual(filtered["resumen"]["total"], 1)
        self.assertEqual(filtered["oficios"], [{"nombre": "Carpintería", "solicitudes": 1}])
        self.assertEqual(filtered["departamentos"], [{"nombre": "Cortés", "solicitudes": 1}])

    def test_empty_default_range_and_personal_roles_are_rejected(self):
        response = Response()
        report = demand_report(response, None, None, None, None, None, {"uid": "admin"}, self.db)
        self.assertEqual(len(report["dias"]), 30)
        self.assertEqual(report["resumen"]["total"], 0)
        self.assertEqual(report["destacados"]["semana"], [])
        self.assertEqual(response.headers["Cache-Control"], "no-store, private")
        with self.assertRaises(HTTPException) as forbidden:
            demand_report(Response(), None, None, None, None, None, {"uid": "cliente"}, self.db)
        self.assertEqual(forbidden.exception.status_code, 403)
        with self.assertRaises(HTTPException) as invalid:
            get_demand_report(self.db, date(2025, 1, 1), date(2026, 1, 2))
        self.assertEqual(invalid.exception.status_code, 422)

    def test_weekday_averages_account_for_unequal_occurrences_and_unknown_places(self):
        for day in (9, 9, 10):
            self.add_request(OpenRequest, datetime(2026, 11, day, 12, tzinfo=timezone.utc),
                             municipality="San José" if day == 9 else "Dirección no estructurada")
        self.db.commit()
        result = get_demand_report(self.db, date(2026, 11, 9), date(2026, 11, 16))
        monday, tuesday = result["semana"][:2]
        self.assertEqual((monday["solicitudes"], monday["dias"], monday["promedio"]), (2, 2, 1))
        self.assertEqual((tuesday["solicitudes"], tuesday["dias"], tuesday["promedio"]), (1, 1, 1))
        self.assertEqual([day["nombre"] for day in result["destacados"]["semana"]], ["Lunes", "Martes"])
        self.assertEqual(result["departamentos"], [{"nombre": "Sin identificar", "solicitudes": 3}])
        self.assertNotIn("Dirección no estructurada", str(result))
