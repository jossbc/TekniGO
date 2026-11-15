from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest
import uuid

from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.direct_requests_router import create_request, get_budget_estimate,register_adjustment,update_payment_method
from app.direct_requests_schemas import CreateDirectRequest, EstimateRequest, RegisterAdjustmentRequest, UpdatePaymentMethodRequest
from app.main import app as fastapi_app
from app.models import (
    CoverageZone,
    DirectRequest,
    IdentityDocument,
    IdentityVerificationRequest,
    Service,
    Specialty,
    TechnicianProfile,
    User,
    UserRole,
)
from app.technician_direct_requests import accept_my_direct_request_endpoint, get_my_direct_requests_endpoint


class DirectRequestsEndpointTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        self.client_user = self._add_user("client-uid-1", "Joseline Banegas", "CLIENTE", "1")
        self.tech_only_user = self._add_user("tech-only-uid", "Sin Rol Cliente", "TECNICO", "2")
        self.tech_user = self._add_user("tech-uid-1", "Kristhian Benitez", "TECNICO", "3")

        self.db.add(
            TechnicianProfile(
                usuario_id=self.tech_user.id,
                disponible=True,
                estado_verificacion="VERIFICADO",
            )
        )
        self._approve_technician_docs(self.tech_user.id)

        self.db.add(
            CoverageZone(
                usuario_id=self.tech_user.id,
                departamento="Francisco Morazán",
                municipio="Distrito Central",
                sector="Col. Palmira",
            )
        )

        self.specialty = Specialty(nombre="Electricidad")
        self.db.add(self.specialty)
        self.db.flush()

        self.service = Service(
            usuario_id=self.tech_user.id,
            especialidad_id=self.specialty.id,
            nombre="Cambio de tomacorrientes",
            descripcion="Instalación residencial segura",
            tarifa_base=Decimal("250.00"),
            unidad_cobro="POR_SERVICIO",
            activo=True,
        )
        self.db.add(self.service)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _add_user(self, uid: str, name: str, role: str, suffix: str) -> User:
        user = User(
            firebase_uid=uid,
            correo=f"{uid}@example.com",
            nombre_completo=name,
            dni=f"080120000000{suffix}",
            telefono=f"+5049000000{suffix}",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add(user)
        self.db.flush()
        self.db.add(UserRole(usuario_id=user.id, rol=role))
        self.db.flush()
        return user

    def _approve_technician_docs(self, user_id: uuid.UUID) -> None:
        verification_req = IdentityVerificationRequest(usuario_id=user_id, version=1)
        self.db.add(verification_req)
        self.db.flush()
        today = date.today()
        doc_types = (
            "DNI_FRONTAL",
            "DNI_REVERSO",
            "SELFIE",
            "ANTECEDENTES_POLICIALES",
            "ANTECEDENTES_PENALES",
        )
        for doc_type in doc_types:
            is_antecedente = doc_type.startswith("ANTECEDENTES_")
            self.db.add(
                IdentityDocument(
                    usuario_id=user_id,
                    solicitud_id=verification_req.id,
                    tipo=doc_type,
                    ruta_privada=f"private/{user_id}/{doc_type}.jpg",
                    content_type="image/jpeg",
                    tamano_bytes=1024,
                    sha256="a" * 64,
                    estado="APROBADO",
                    fecha_emision_declarada=today if is_antecedente else None,
                    fecha_vencimiento=today + timedelta(days=60) if is_antecedente else None,
                )
            )

    def _valid_form_payload(self, **overrides) -> CreateDirectRequest:
        data = {
            "tecnico_id": str(self.tech_user.id),
            "servicio_id": str(self.service.id),
            "municipio": "Distrito Central",
            "sector": "Col. Palmira",
            "direccion_exacta": "Calle principal, casa #12",
            "fecha_hora": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
            "descripcion": "Necesito cambiar 2 tomacorrientes en la sala.",
            "cantidad": Decimal("2"),
            "metodo_pago": "Efectivo",
        }
        data.update(overrides)
        return CreateDirectRequest(**data)

    def test_routes_are_registered_on_fastapi_app(self):
        paths = fastapi_app.openapi()["paths"]
        self.assertIn("post", paths["/direct-requests/"])
        self.assertIn("post", paths["/direct-requests/estimate"])

    def test_estimate_budget_calculates_total_and_handles_invalid_service_id(self):
        response = Response()
        estimate = get_budget_estimate(
            request=EstimateRequest(servicio_id=str(self.service.id), cantidad=Decimal("3")),
            response=response,
            db=self.db,
            current_user={"uid": self.client_user.firebase_uid},
        )
        self.assertEqual(estimate.total, Decimal("750.00"))
        self.assertEqual(response.headers.get("Cache-Control"), "no-store, private")

        with self.assertRaises(HTTPException) as ctx:
            get_budget_estimate(
                request=EstimateRequest(servicio_id="not-a-uuid", cantidad=Decimal("1")),
                response=Response(),
                db=self.db,
                current_user={"uid": self.client_user.firebase_uid},
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_create_direct_request_persists_pendiente_and_reaches_technician_panel(self):
        response = Response()
        payload = self._valid_form_payload(metodo_pago="efectivo")
        result = create_request(
            request=payload,
            response=response,
            db=self.db,
            current_user={"uid": self.client_user.firebase_uid},
        )

        self.assertEqual(result["message"], "Solicitud creada exitosamente")
        self.assertEqual(result["estado"], "PENDIENTE")
        self.assertEqual(response.headers.get("Cache-Control"), "no-store, private")

        created = self.db.scalar(select(DirectRequest).where(DirectRequest.id == result["id"]))
        self.assertIsNotNone(created)
        self.assertEqual(created.cliente_id, self.client_user.firebase_uid)
        self.assertEqual(created.tecnico_id, self.tech_user.firebase_uid)
        self.assertEqual(created.estado, "PENDIENTE")
        self.assertEqual(created.monto_base, Decimal("500.00"))
        self.assertEqual(created.metodo_pago, "efectivo")

        tech_list = get_my_direct_requests_endpoint(
            response=Response(),
            claims={"uid": self.tech_user.firebase_uid},
            db=self.db,
        )
        self.assertEqual(len(tech_list), 1)
        self.assertEqual(tech_list[0]["estado"], "PENDIENTE")
        self.assertEqual(tech_list[0]["total_estimado"], 500.0)

        accepted = accept_my_direct_request_endpoint(
            solicitud_id=result["id"],
            response=Response(),
            claims={"uid": self.tech_user.firebase_uid},
            db=self.db,
        )
        self.assertEqual(accepted["estado"], "ACEPTADA")

    def test_unregistered_client_returns_404_instead_of_database_fk_error(self):
        with self.assertRaises(HTTPException) as ctx:
            create_request(
                request=self._valid_form_payload(),
                response=Response(),
                db=self.db,
                current_user={"uid": "unregistered-firebase-uid"},
            )
        self.assertEqual(ctx.exception.status_code, 404)

    def test_non_client_account_cannot_submit_direct_request(self):
        with self.assertRaises(HTTPException) as ctx:
            create_request(
                request=self._valid_form_payload(),
                response=Response(),
                db=self.db,
                current_user={"uid": self.tech_only_user.firebase_uid},
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_invalid_service_id_past_date_uncovered_municipality_and_blank_fields(self):
        with self.assertRaises(HTTPException) as bad_service:
            create_request(
                request=self._valid_form_payload(servicio_id="not-a-uuid"),
                response=Response(),
                db=self.db,
                current_user={"uid": self.client_user.firebase_uid},
            )
        self.assertEqual(bad_service.exception.status_code, 400)

        with self.assertRaises(HTTPException) as past_date:
            create_request(
                request=self._valid_form_payload(
                    fecha_hora=(datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
                ),
                response=Response(),
                db=self.db,
                current_user={"uid": self.client_user.firebase_uid},
            )
        self.assertEqual(past_date.exception.status_code, 400)

        with self.assertRaises(HTTPException) as bad_muni:
            create_request(
                request=self._valid_form_payload(municipio="San Pedro Sula"),
                response=Response(),
                db=self.db,
                current_user={"uid": self.client_user.firebase_uid},
            )
        self.assertEqual(bad_muni.exception.status_code, 400)

        with self.assertRaises(ValidationError):
            self._valid_form_payload(direccion_exacta="   ")

        def test_hu37_cash_flow_creates_and_accepts_without_digital_charge(self):
         """HU-37 CA-01 & CA-03: Con efectivo no se invoca cobro digital y el técnico puede aceptar directamente."""
        payload = self._valid_form_payload(metodo_pago="efectivo")
        result = create_request(
            request=payload,
            response=Response(),
            db=self.db,
            current_user={"uid": self.client_user.firebase_uid},
        )
        solicitud_id = result["id"]

        # El técnico acepta sin necesidad de pasarela previa
        accepted = accept_my_direct_request_endpoint(
            solicitud_id=solicitud_id,
            response=Response(),
            claims={"uid": self.tech_user.firebase_uid},
            db=self.db,
        )
        self.assertEqual(accepted["estado"], "ACEPTADA")
        self.assertEqual(accepted["metodo_pago"], "efectivo")
        self.assertIsNone(accepted["referencia_pasarela"])
        self.assertEqual(accepted["desglose_medios"]["metodo_pago_base"], "efectivo")

    def test_hu37_card_flow_requires_gateway_reference_before_accepted(self):
        """HU-37 CA-03 & CA-05: Con tarjeta se requiere pasarela antes de Aceptada y no persiste datos de tarjeta."""
        payload = self._valid_form_payload(metodo_pago="tarjeta")
        result = create_request(
            request=payload,
            response=Response(),
            db=self.db,
            current_user={"uid": self.client_user.firebase_uid},
        )
        solicitud_id = result["id"]

        # Intento de aceptar sin pago con tarjeta previo debe arrojar 402 Payment Required
        with self.assertRaises(HTTPException) as pay_err:
            accept_my_direct_request_endpoint(
                solicitud_id=solicitud_id,
                response=Response(),
                claims={"uid": self.tech_user.firebase_uid},
                db=self.db,
            )
        self.assertEqual(pay_err.exception.status_code, 402)
        self.assertIn("pasarela", pay_err.exception.detail.lower())

        # Cliente autoriza pago en pasarela segura y actualiza referencia
        update_payment_method(
            solicitud_id=solicitud_id,
            request=UpdatePaymentMethodRequest(
                metodo_pago="tarjeta",
                referencia_pasarela="TKGO-GW-AUTH-999888",
            ),
            response=Response(),
            db=self.db,
            current_user={"uid": self.client_user.firebase_uid},
        )

        # Ahora el técnico sí puede aceptar
        accepted = accept_my_direct_request_endpoint(
            solicitud_id=solicitud_id,
            response=Response(),
            claims={"uid": self.tech_user.firebase_uid},
            db=self.db,
        )
        self.assertEqual(accepted["estado"], "ACEPTADA")
        self.assertEqual(accepted["referencia_pasarela"], "TKGO-GW-AUTH-999888")
        self.assertEqual(accepted["desglose_medios"]["referencia_pasarela_base"], "TKGO-GW-AUTH-999888")

    def test_hu37_invalid_and_empty_methods_rejected(self):
        """HU-37 CA-02: No se permite método vacío, desconocido o no autorizado."""
        with self.assertRaises(ValidationError):
            self._valid_form_payload(metodo_pago="")

        with self.assertRaises(ValidationError):
            self._valid_form_payload(metodo_pago="Transferencia")

        with self.assertRaises(ValidationError):
            self._valid_form_payload(metodo_pago="cripto")

    def test_hu37_cannot_change_payment_method_after_accepted(self):
        """HU-37 CA-04: Una contratación ya aceptada conserva el método inicial."""
        payload = self._valid_form_payload(metodo_pago="efectivo")
        result = create_request(
            request=payload,
            response=Response(),
            db=self.db,
            current_user={"uid": self.client_user.firebase_uid},
        )
        solicitud_id = result["id"]

        # Se acepta la solicitud
        accept_my_direct_request_endpoint(
            solicitud_id=solicitud_id,
            response=Response(),
            claims={"uid": self.tech_user.firebase_uid},
            db=self.db,
        )

        # Intento de cambiar método base tras ser aceptada debe fallar con 409 Conflict
        with self.assertRaises(HTTPException) as conflict_err:
            update_payment_method(
                solicitud_id=solicitud_id,
                request=UpdatePaymentMethodRequest(metodo_pago="tarjeta"),
                response=Response(),
                db=self.db,
                current_user={"uid": self.client_user.firebase_uid},
            )
        self.assertEqual(conflict_err.exception.status_code, 409)
        self.assertIn("conserva el método inicial", conflict_err.exception.detail)

    def test_hu37_approved_adjustment_can_have_distinct_method_with_decimal_arithmetic(self):
        """HU-37 CA-04 y Dinero: El ajuste puede registrar un medio distinto solo para su diferencia aprobada."""
        payload = self._valid_form_payload(metodo_pago="efectivo")
        result = create_request(
            request=payload,
            response=Response(),
            db=self.db,
            current_user={"uid": self.client_user.firebase_uid},
        )
        solicitud_id = result["id"]

        # Se acepta el acuerdo base por L. 500.00 en efectivo
        accept_my_direct_request_endpoint(
            solicitud_id=solicitud_id,
            response=Response(),
            claims={"uid": self.tech_user.firebase_uid},
            db=self.db,
        )

        # Se registra un ajuste aprobado por L. 150.50 pagadero con tarjeta
        adj_result = register_adjustment(
            solicitud_id=solicitud_id,
            request=RegisterAdjustmentRequest(
                monto_ajuste=Decimal("150.50"),
                metodo_pago_ajuste="tarjeta",
                referencia_pasarela_ajuste="TKGO-GW-ADJ-CENTAVOS-150",
            ),
            response=Response(),
            db=self.db,
            current_user={"uid": self.client_user.firebase_uid},
        )

        desglose = adj_result["desglose_medios"]
        self.assertEqual(desglose["monto_base"], "500.00")
        self.assertEqual(desglose["metodo_pago_base"], "efectivo")
        self.assertEqual(desglose["monto_ajuste"], "150.50")
        self.assertEqual(desglose["metodo_pago_ajuste"], "tarjeta")
        self.assertEqual(desglose["referencia_pasarela_ajuste"], "TKGO-GW-ADJ-CENTAVOS-150")
        self.assertEqual(desglose["monto_total"], "650.50")


if __name__ == "__main__":
    unittest.main()
