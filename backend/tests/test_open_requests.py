from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from fastapi import FastAPI, HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.client_requests import list_client_requests
from app.database import Base
from app.models import (
    CoverageZone,
    OpenRequest,
    Service,
    Specialty,
    TechnicianProfile,
    TechnicianSpecialty,
    User,
    UserRole,
)
from app.open_requests import (
    CreateOpenRequestSchema,
    calculate_suggested_range,
    get_open_request_for_viewer,
    get_suggested_range_endpoint,
    list_available_open_requests_for_technician,
    publish_open_request,
    publish_open_request_endpoint,
    router,
)


class OpenRequestsSt01Tests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        self.client = User(
            firebase_uid="client-verified",
            correo="ana@example.com",
            nombre_completo="Ana Cliente",
            dni="0801200000001",
            telefono="+50498765432",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add(self.client)
        self.db.flush()
        self.db.add(UserRole(usuario_id=self.client.id, rol="CLIENTE"))

        self.specialty = Specialty(nombre="Plomería")
        self.db.add(self.specialty)
        self.db.commit()

        self.firebase_identity = SimpleNamespace(
            email="ana@example.com",
            email_verified=True,
            disabled=False,
            phone_number="+50498765432",
        )
        provider = patch("app.email_verification.auth.get_user", return_value=self.firebase_identity)
        provider.start()
        self.addCleanup(provider.stop)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _valid_payload(self, **overrides) -> CreateOpenRequestSchema:
        base = {
            "especialidad_id": self.specialty.id,
            "descripcion": "Reparación de fuga de agua en tubería principal de cocina",
            "municipio": "Distrito Central",
            "sector": "Col. Palmira",
            "direccion_exacta": "Avenida República de Panamá, casa #24",
            "fecha_hora": datetime.now(timezone.utc) + timedelta(days=2),
            "metodo_pago": "efectivo",
            "presupuesto": Decimal("850.00"),
        }
        base.update(overrides)
        return CreateOpenRequestSchema(**base)

    def test_st01_verified_unsuspended_client_publishes_open_request_in_publicada_state(self):
        app = FastAPI()
        app.include_router(router)
        self.assertIn("/open-requests", app.openapi()["paths"])

        response = Response()
        result = publish_open_request_endpoint(
            payload=self._valid_payload(),
            response=response,
            claims={"uid": self.client.firebase_uid},
            db=self.db,
        )
        self.assertIn("no-store", response.headers["cache-control"])
        self.assertIsNotNone(result["id"])
        self.assertEqual(result["solicitud_id"], result["id"])
        self.assertEqual(result["estado"], "PUBLICADA")
        self.assertEqual(result["modalidad"], "ABIERTA")
        self.assertIsNone(result["tecnico_id"])
        self.assertEqual(result["presupuesto"], 850.0)
        self.assertEqual(result["especialidad"]["nombre"], "Plomería")

        stored = self.db.get(OpenRequest, result["id"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored.estado, "Publicada")
        self.assertEqual(stored.modalidad, "ABIERTA")
        self.assertIsNone(stored.tecnico_id)

    def test_ca01_unverified_or_suspended_client_cannot_publish(self):
        # Cliente no verificado en identidad
        self.client.estado_identidad = "EN_REVISION"
        self.db.commit()
        with self.assertRaises(HTTPException) as unverified_err:
            publish_open_request(self._valid_payload(), {"uid": self.client.firebase_uid}, self.db)
        self.assertEqual(unverified_err.exception.status_code, 403)

        # Restaurar identidad pero suspender cuenta asociada
        self.client.estado_identidad = "VERIFICADO"
        self.db.add(UserRole(usuario_id=self.client.id, rol="TECNICO"))
        self.db.add(TechnicianProfile(usuario_id=self.client.id, suspendido=True))
        self.db.commit()

        with self.assertRaises(HTTPException) as suspended_err:
            publish_open_request(self._valid_payload(), {"uid": self.client.firebase_uid}, self.db)
        self.assertEqual(suspended_err.exception.status_code, 403)
        self.assertIn("suspensión", suspended_err.exception.detail)

    def test_ca01_and_ca07_invalid_fields_prevent_publishing(self):
        invalid_cases = [
            {"especialidad_id": uuid.uuid4()},
            {"descripcion": "Corta"},
            {"municipio": "   "},
            {"sector": ""},
            {"direccion_exacta": "123"},
            {"fecha_hora": datetime.now(timezone.utc) - timedelta(hours=1)},
            {"metodo_pago": "transferencia"},
            {"presupuesto": Decimal("0")},
            {"presupuesto": Decimal("-150.00")},
        ]
        for override in invalid_cases:
            with self.subTest(override=override):
                with self.assertRaises(HTTPException) as err:
                    publish_open_request(
                        self._valid_payload(**override),
                        {"uid": self.client.firebase_uid},
                        self.db,
                    )
                self.assertEqual(err.exception.status_code, 400)

    def test_st03_suggested_range_by_category_and_unit_and_no_invented_range_when_empty(self):
        # CA-06: Sin tarifas comparables se informa que no hay referencia suficiente y no se inventa rango
        empty_range = calculate_suggested_range(self.specialty.id, self.db)
        self.assertFalse(empty_range["tiene_referencia"])
        self.assertEqual(empty_range["servicios_comparables"], 0)
        self.assertIsNone(empty_range["tarifa_promedio"])
        self.assertIsNone(empty_range["rango_sugerido_min"])
        self.assertIsNone(empty_range["rango_sugerido_max"])
        self.assertIn("No hay referencia suficiente", empty_range["mensaje"])

        # Crear dos técnicos con servicios activos en Plomería y uno inactivo / otro suspendido
        tech_1 = User(
            firebase_uid="tech-r1",
            correo="t1@example.com",
            nombre_completo="Técnico 1",
            dni="0801200000011",
            telefono="+50490000011",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        tech_2 = User(
            firebase_uid="tech-r2",
            correo="t2@example.com",
            nombre_completo="Técnico 2",
            dni="0801200000012",
            telefono="+50490000012",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        tech_suspended = User(
            firebase_uid="tech-susp",
            correo="ts@example.com",
            nombre_completo="Técnico Suspendido",
            dni="0801200000013",
            telefono="+50490000013",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add_all([tech_1, tech_2, tech_suspended])
        self.db.flush()
        self.db.add_all(
            [
                TechnicianProfile(usuario_id=tech_1.id, disponible=True, estado_verificacion="VERIFICADO"),
                TechnicianProfile(usuario_id=tech_2.id, disponible=True, estado_verificacion="VERIFICADO"),
                TechnicianProfile(usuario_id=tech_suspended.id, disponible=True, suspendido=True),
            ]
        )
        self.db.add_all(
            [
                Service(
                    usuario_id=tech_1.id,
                    especialidad_id=self.specialty.id,
                    nombre="Reparación de tubería",
                    descripcion="Servicio completo de plomería",
                    tarifa_base=Decimal("400.00"),
                    unidad_cobro="POR_SERVICIO",
                    activo=True,
                ),
                Service(
                    usuario_id=tech_2.id,
                    especialidad_id=self.specialty.id,
                    nombre="Desinfección y ajuste de llave",
                    descripcion="Servicio de plomería residencial",
                    tarifa_base=Decimal("600.00"),
                    unidad_cobro="POR_SERVICIO",
                    activo=True,
                ),
                Service(
                    usuario_id=tech_1.id,
                    especialidad_id=self.specialty.id,
                    nombre="Diagnóstico por hora",
                    descripcion="Revisión técnica por hora",
                    tarifa_base=Decimal("250.00"),
                    unidad_cobro="POR_HORA",
                    activo=True,
                ),
                # Servicio inactivo: no debe incluirse
                Service(
                    usuario_id=tech_2.id,
                    especialidad_id=self.specialty.id,
                    nombre="Servicio desactivado",
                    descripcion="No disponible actualmente",
                    tarifa_base=Decimal("5000.00"),
                    unidad_cobro="POR_SERVICIO",
                    activo=False,
                ),
                # Servicio de técnico suspendido: no debe incluirse
                Service(
                    usuario_id=tech_suspended.id,
                    especialidad_id=self.specialty.id,
                    nombre="Servicio suspendido",
                    descripcion="No elegible",
                    tarifa_base=Decimal("9000.00"),
                    unidad_cobro="POR_SERVICIO",
                    activo=True,
                ),
            ]
        )
        self.db.commit()

        # CA-05: Rango sugerido basado en tarifas promedio de la categoría con su base y unidad
        response = Response()
        suggested = get_suggested_range_endpoint(
            response=response,
            especialidad_id=self.specialty.id,
            unidad_cobro=None,
            claims={"uid": self.client.firebase_uid},
            db=self.db,
        )
        self.assertTrue(suggested["tiene_referencia"])
        self.assertEqual(suggested["unidad_cobro"], "POR_SERVICIO")
        self.assertEqual(suggested["servicios_comparables"], 2)
        self.assertEqual(suggested["tarifa_promedio"], 500.0)
        self.assertEqual(suggested["tarifa_minima"], 400.0)
        self.assertEqual(suggested["tarifa_maxima"], 600.0)
        self.assertEqual(suggested["rango_sugerido_min"], 425.0)
        self.assertEqual(suggested["rango_sugerido_max"], 575.0)

        # Filtrando explícitamente por unidad POR_HORA
        by_hour = calculate_suggested_range(self.specialty.id, self.db, unidad_cobro="POR_HORA")
        self.assertTrue(by_hour["tiene_referencia"])
        self.assertEqual(by_hour["unidad_cobro"], "POR_HORA")
        self.assertEqual(by_hour["tarifa_promedio"], 250.0)

        # Si se exige un mínimo parametrizado mayor (ej. min_comparables=3), informa falta de referencia (CA-06)
        insufficient = calculate_suggested_range(
            self.specialty.id,
            self.db,
            unidad_cobro="POR_SERVICIO",
            min_comparables=3,
        )
        self.assertFalse(insufficient["tiene_referencia"])
        self.assertIsNone(insufficient["rango_sugerido_min"])

        # CA-05: El rango sugerido no sustituye el monto elegido por el cliente al publicar
        published = publish_open_request(
            self._valid_payload(presupuesto=Decimal("320.00")),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        self.assertEqual(published["presupuesto"], 320.0)

    def test_st04_eligibility_visibility_and_exact_location_and_phone_protection(self):
        created = publish_open_request(
            self._valid_payload(
                municipio="Distrito Central",
                sector="Col. Palmira",
                direccion_exacta="Edificio Torre Azul, apto 502, portón gris",
            ),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        req_id = created["id"]

        # Técnico elegible y compatible en especialidad + cobertura
        tech_ok = User(
            firebase_uid="tech-eligible",
            correo="ok@example.com",
            nombre_completo="Técnico Elegible",
            dni="0801200000021",
            telefono="+50490000021",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        # Técnico verificado pero de otra zona de cobertura
        tech_other_zone = User(
            firebase_uid="tech-other-zone",
            correo="zone@example.com",
            nombre_completo="Técnico Otra Zona",
            dni="0801200000022",
            telefono="+50490000022",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        # Técnico no disponible o restringido
        tech_unavailable = User(
            firebase_uid="tech-unavailable",
            correo="unav@example.com",
            nombre_completo="Técnico No Disponible",
            dni="0801200000023",
            telefono="+50490000023",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add_all([tech_ok, tech_other_zone, tech_unavailable])
        self.db.flush()

        for u in (tech_ok, tech_other_zone, tech_unavailable):
            self.db.add(UserRole(usuario_id=u.id, rol="TECNICO"))

        self.db.add_all(
            [
                TechnicianProfile(usuario_id=tech_ok.id, disponible=True, estado_verificacion="VERIFICADO"),
                TechnicianProfile(usuario_id=tech_other_zone.id, disponible=True, estado_verificacion="VERIFICADO"),
                TechnicianProfile(usuario_id=tech_unavailable.id, disponible=False, estado_verificacion="VERIFICADO"),
                TechnicianSpecialty(usuario_id=tech_ok.id, especialidad_id=self.specialty.id),
                TechnicianSpecialty(usuario_id=tech_other_zone.id, especialidad_id=self.specialty.id),
                TechnicianSpecialty(usuario_id=tech_unavailable.id, especialidad_id=self.specialty.id),
                CoverageZone(
                    usuario_id=tech_ok.id,
                    departamento="Francisco Morazán",
                    municipio="Distrito Central",
                    sector="Col. Palmira",
                ),
                CoverageZone(
                    usuario_id=tech_other_zone.id,
                    departamento="Cortés",
                    municipio="San Pedro Sula",
                    sector=" Río de Piedras",
                ),
                CoverageZone(
                    usuario_id=tech_unavailable.id,
                    departamento="Francisco Morazán",
                    municipio="Distrito Central",
                    sector="Col. Palmira",
                ),
            ]
        )
        self.db.commit()

        # CA-03 y CA-04: Técnico elegible y compatible ve la solicitud con municipio y sector, pero SIN dirección exacta ni teléfono
        available_ok = list_available_open_requests_for_technician(
            {"uid": tech_ok.firebase_uid},
            self.db,
        )
        self.assertEqual(available_ok["total"], 1)
        item = available_ok["solicitudes"][0]
        self.assertEqual(item["id"], req_id)
        self.assertEqual(item["municipio"], "Distrito Central")
        self.assertEqual(item["sector"], "Col. Palmira")
        self.assertTrue(item["ubicacion_exacta_protegida"])
        self.assertNotIn("direccion_exacta", item)
        self.assertNotIn("telefono", item["cliente"])

        detail_before_accept = get_open_request_for_viewer(
            req_id,
            {"uid": tech_ok.firebase_uid},
            self.db,
        )
        self.assertNotIn("direccion_exacta", detail_before_accept)
        self.assertNotIn("telefono", detail_before_accept["cliente"])

        # CA-04: Técnico de otra zona no recibe la solicitud en su listado ni puede abrirla por ID
        available_other = list_available_open_requests_for_technician(
            {"uid": tech_other_zone.firebase_uid},
            self.db,
        )
        self.assertEqual(available_other["total"], 0)
        with self.assertRaises(HTTPException) as zone_err:
            get_open_request_for_viewer(req_id, {"uid": tech_other_zone.firebase_uid}, self.db)
        self.assertEqual(zone_err.exception.status_code, 403)

        # CA-04: Técnico no disponible es bloqueado con 403
        with self.assertRaises(HTTPException) as unav_err:
            list_available_open_requests_for_technician({"uid": tech_unavailable.firebase_uid}, self.db)
        self.assertEqual(unav_err.exception.status_code, 403)

        # CA-03: Cuando la solicitud pasa a Aceptada y se adjudica a tech_ok, solo tech_ok recibe dirección exacta y teléfono
        record = self.db.get(OpenRequest, req_id)
        record.tecnico_id = tech_ok.firebase_uid
        record.estado = "Aceptada"
        self.db.commit()

        detail_after_accept = get_open_request_for_viewer(
            req_id,
            {"uid": tech_ok.firebase_uid},
            self.db,
        )
        self.assertFalse(detail_after_accept["ubicacion_exacta_protegida"])
        self.assertEqual(
            detail_after_accept["direccion_exacta"],
            "Edificio Torre Azul, apto 502, portón gris",
        )
        self.assertEqual(detail_after_accept["cliente"]["telefono"], "+50498765432")

        # Otro técnico (incluso si se le agrega la zona) no puede ver una solicitud ya adjudicada a otro
        self.db.add(
            CoverageZone(
                usuario_id=tech_other_zone.id,
                departamento="Francisco Morazán",
                municipio="Distrito Central",
                sector="Col. Palmira",
            )
        )
        self.db.commit()
        with self.assertRaises(HTTPException) as not_awarded_err:
            get_open_request_for_viewer(req_id, {"uid": tech_other_zone.firebase_uid}, self.db)
        self.assertEqual(not_awarded_err.exception.status_code, 403)

    def test_st05_publication_lack_of_references_geographic_filters_and_privacy_matrix(self):
        other_specialty = Specialty(nombre="Refrigeración")
        other_client = User(
            firebase_uid="client-foreign",
            correo="foreign@example.com",
            nombre_completo="Otro Cliente",
            dni="0801200000099",
            telefono="+50490000099",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add_all([other_specialty, other_client])
        self.db.flush()
        self.db.add(UserRole(usuario_id=other_client.id, rol="CLIENTE"))
        self.db.commit()

        # 1. Falta de referencias en la nueva especialidad no inventa rango (CA-06)
        no_ref = calculate_suggested_range(other_specialty.id, self.db)
        self.assertFalse(no_ref["tiene_referencia"])
        self.assertIsNone(no_ref["rango_sugerido_min"])
        self.assertIsNone(no_ref["rango_sugerido_max"])

        # 2. Publicación de solicitud abierta e integración con el listado del cliente
        published = publish_open_request(
            self._valid_payload(
                especialidad_id=other_specialty.id,
                municipio="Distrito Central",
                sector="Col. Tepeyac",
                direccion_exacta="Calle Real, casa 88, portón negro",
                presupuesto=Decimal("950.00"),
            ),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        req_id = published["id"]
        client_list = list_client_requests(
            {"uid": self.client.firebase_uid},
            self.db,
            modalidad="ABIERTA",
            estado="PUBLICADA",
        )
        self.assertEqual(client_list["total"], 1)
        self.assertEqual(client_list["solicitudes"][0]["id"], req_id)

        # 3. Otro cliente no puede acceder por API a la solicitud abierta de self.client
        with self.assertRaises(HTTPException) as foreign_client_err:
            get_open_request_for_viewer(req_id, {"uid": other_client.firebase_uid}, self.db)
        self.assertEqual(foreign_client_err.exception.status_code, 403)

        # 4. Filtros geográficos por municipio + sector y restricciones operativas
        tech_same_muni_diff_sector = User(
            firebase_uid="tech-diff-sector",
            correo="ds@example.com",
            nombre_completo="Técnico Otro Sector",
            dni="0801200000031",
            telefono="+50490000031",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        tech_same_zone_diff_spec = User(
            firebase_uid="tech-diff-spec",
            correo="dsp@example.com",
            nombre_completo="Técnico Otra Especialidad",
            dni="0801200000032",
            telefono="+50490000032",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        tech_financial_restriction = User(
            firebase_uid="tech-fin-rest",
            correo="fin@example.com",
            nombre_completo="Técnico Restringido Financiero",
            dni="0801200000033",
            telefono="+50490000033",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add_all([tech_same_muni_diff_sector, tech_same_zone_diff_spec, tech_financial_restriction])
        self.db.flush()
        for u in (tech_same_muni_diff_sector, tech_same_zone_diff_spec, tech_financial_restriction):
            self.db.add(UserRole(usuario_id=u.id, rol="TECNICO"))

        self.db.add_all(
            [
                TechnicianProfile(
                    usuario_id=tech_same_muni_diff_sector.id,
                    disponible=True,
                    estado_verificacion="VERIFICADO",
                ),
                TechnicianProfile(
                    usuario_id=tech_same_zone_diff_spec.id,
                    disponible=True,
                    estado_verificacion="VERIFICADO",
                ),
                TechnicianProfile(
                    usuario_id=tech_financial_restriction.id,
                    disponible=True,
                    estado_verificacion="VERIFICADO",
                    restriccion_financiera=True,
                ),
                TechnicianSpecialty(
                    usuario_id=tech_same_muni_diff_sector.id,
                    especialidad_id=other_specialty.id,
                ),
                TechnicianSpecialty(
                    usuario_id=tech_same_zone_diff_spec.id,
                    especialidad_id=self.specialty.id,  # Plomería != Refrigeración
                ),
                TechnicianSpecialty(
                    usuario_id=tech_financial_restriction.id,
                    especialidad_id=other_specialty.id,
                ),
                CoverageZone(
                    usuario_id=tech_same_muni_diff_sector.id,
                    departamento="Francisco Morazán",
                    municipio="Distrito Central",
                    sector="Col. Kennedy",  # Sector distinto a Col. Tepeyac
                ),
                CoverageZone(
                    usuario_id=tech_same_zone_diff_spec.id,
                    departamento="Francisco Morazán",
                    municipio="Distrito Central",
                    sector="Col. Tepeyac",
                ),
                CoverageZone(
                    usuario_id=tech_financial_restriction.id,
                    departamento="Francisco Morazán",
                    municipio="Distrito Central",
                    sector="Col. Tepeyac",
                ),
            ]
        )
        self.db.commit()

        # Mismo municipio pero distinto sector -> 0 resultados y 403 al consultar por ID
        self.assertEqual(
            list_available_open_requests_for_technician(
                {"uid": tech_same_muni_diff_sector.firebase_uid},
                self.db,
            )["total"],
            0,
        )
        with self.assertRaises(HTTPException) as sector_err:
            get_open_request_for_viewer(
                req_id,
                {"uid": tech_same_muni_diff_sector.firebase_uid},
                self.db,
            )
        self.assertEqual(sector_err.exception.status_code, 403)

        # Misma zona pero distinta especialidad -> 0 resultados y 403 al consultar por ID
        self.assertEqual(
            list_available_open_requests_for_technician(
                {"uid": tech_same_zone_diff_spec.firebase_uid},
                self.db,
            )["total"],
            0,
        )
        with self.assertRaises(HTTPException) as spec_err:
            get_open_request_for_viewer(
                req_id,
                {"uid": tech_same_zone_diff_spec.firebase_uid},
                self.db,
            )
        self.assertEqual(spec_err.exception.status_code, 403)

        # Técnico con restricción financiera -> bloqueado con 403
        with self.assertRaises(HTTPException) as fin_err:
            list_available_open_requests_for_technician(
                {"uid": tech_financial_restriction.firebase_uid},
                self.db,
            )
        self.assertEqual(fin_err.exception.status_code, 403)
