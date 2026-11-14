from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from fastapi import FastAPI, HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.client_requests import get_client_request_detail, list_client_requests
from app.database import Base
from app.models import (
    CoverageZone,
    DiscardedOpenRequest,
    OpenRequest,
    OpenRequestProposal,
    Service,
    Specialty,
    TechnicianProfile,
    TechnicianSpecialty,
    User,
    UserRole,
)
from app.open_requests import (
    CreateOpenRequestSchema,
    accept_published_budget_endpoint,
    accept_published_budget_for_open_request,
    calculate_suggested_range,
    discard_open_request_for_technician,
    get_open_request_for_viewer,
    get_suggested_range_endpoint,
    list_available_open_requests_for_technician,
    list_coverage_zones_catalog,
    list_open_request_proposals_for_viewer,
    publish_open_request,
    publish_open_request_endpoint,
    router,
    validate_technician_can_bid_on_open_request,
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

    def test_hu27_st01_query_opportunities_by_coverage_trade_and_state(self):
        spec_elec = Specialty(nombre="Electricidad")
        spec_carp = Specialty(nombre="Carpintería")
        tech = User(
            firebase_uid="tech-hu27-st01",
            correo="hu27@example.com",
            nombre_completo="Técnico Multioficio",
            dni="0801200000271",
            telefono="+50490000271",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add_all([spec_elec, spec_carp, tech])
        self.db.flush()
        self.db.add(UserRole(usuario_id=tech.id, rol="TECNICO"))
        self.db.add(TechnicianProfile(usuario_id=tech.id, disponible=True, estado_verificacion="VERIFICADO"))
        self.db.add_all(
            [
                TechnicianSpecialty(usuario_id=tech.id, especialidad_id=self.specialty.id),  # Plomería
                TechnicianSpecialty(usuario_id=tech.id, especialidad_id=spec_elec.id),  # Electricidad
                CoverageZone(
                    usuario_id=tech.id,
                    departamento="Francisco Morazán",
                    municipio="Distrito Central",
                    sector="Col. Palmira",
                ),
                CoverageZone(
                    usuario_id=tech.id,
                    departamento="Francisco Morazán",
                    municipio="Distrito Central",
                    sector="Col. Florencia",
                ),
            ]
        )
        self.db.commit()

        # 1. Publicada compatible (Plomería - Col. Palmira)
        req_pub = publish_open_request(
            self._valid_payload(
                especialidad_id=self.specialty.id,
                municipio="Distrito Central",
                sector="Col. Palmira",
                presupuesto=Decimal("600.00"),
            ),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        # 2. Con ofertas compatible (Electricidad - Col. Florencia)
        req_offers = publish_open_request(
            self._valid_payload(
                especialidad_id=spec_elec.id,
                municipio="Distrito Central",
                sector="Col. Florencia",
                presupuesto=Decimal("1200.00"),
            ),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        row_offers = self.db.get(OpenRequest, req_offers["id"])
        row_offers.estado = "Con ofertas"

        # 3. Solicitudes NO elegibles como oportunidades (CA-02): Adjudicada/Aceptada, Cancelada, Completada, Otra zona, Otro oficio
        req_awarded = publish_open_request(
            self._valid_payload(
                especialidad_id=self.specialty.id,
                municipio="Distrito Central",
                sector="Col. Palmira",
            ),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        row_awarded = self.db.get(OpenRequest, req_awarded["id"])
        row_awarded.estado = "Aceptada"
        row_awarded.tecnico_id = "other-tech-uid"

        req_cancelled = publish_open_request(
            self._valid_payload(
                especialidad_id=self.specialty.id,
                municipio="Distrito Central",
                sector="Col. Palmira",
            ),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        self.db.get(OpenRequest, req_cancelled["id"]).estado = "Cancelada"

        req_completed = publish_open_request(
            self._valid_payload(
                especialidad_id=self.specialty.id,
                municipio="Distrito Central",
                sector="Col. Palmira",
            ),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        self.db.get(OpenRequest, req_completed["id"]).estado = "Completada"

        # Incompatible por oficio (Carpintería)
        publish_open_request(
            self._valid_payload(
                especialidad_id=spec_carp.id,
                municipio="Distrito Central",
                sector="Col. Palmira",
            ),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        # Incompatible por zona (Col. Miramontes)
        publish_open_request(
            self._valid_payload(
                especialidad_id=self.specialty.id,
                municipio="Distrito Central",
                sector="Col. Miramontes",
            ),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        self.db.commit()

        # CA-01 y CA-02: Consulta general retorna únicamente Publicada y Con ofertas compatibles
        all_compatible = list_available_open_requests_for_technician(
            {"uid": tech.firebase_uid},
            self.db,
        )
        self.assertEqual(all_compatible["total"], 2)
        returned_ids = {item["id"] for item in all_compatible["solicitudes"]}
        self.assertEqual(returned_ids, {req_pub["id"], req_offers["id"]})

        # El detalle también permite consultar una licitación en estado "Con ofertas" protegiendo dirección y teléfono (CA-01, CA-03)
        detail_offers = get_open_request_for_viewer(
            req_offers["id"],
            {"uid": tech.firebase_uid},
            self.db,
        )
        self.assertEqual(detail_offers["id"], req_offers["id"])
        self.assertNotIn("direccion_exacta", detail_offers)
        self.assertNotIn("telefono", detail_offers["cliente"])

        # Filtro por estado CON_OFERTAS / Con ofertas
        only_with_offers = list_available_open_requests_for_technician(
            {"uid": tech.firebase_uid},
            self.db,
            estado="Con ofertas",
        )
        self.assertEqual(only_with_offers["total"], 1)
        self.assertEqual(only_with_offers["solicitudes"][0]["id"], req_offers["id"])

        # Filtro por oficio (Plomería) y sector (Col. Palmira)
        by_trade_and_sector = list_available_open_requests_for_technician(
            {"uid": tech.firebase_uid},
            self.db,
            especialidad_id=self.specialty.id,
            sector="Col. Palmira",
        )
        self.assertEqual(by_trade_and_sector["total"], 1)
        self.assertEqual(by_trade_and_sector["solicitudes"][0]["id"], req_pub["id"])

        # CA-06: Ausencia de coincidencias retorna mensaje y filtros aplicados para poder revisarlos
        no_match = list_available_open_requests_for_technician(
            {"uid": tech.firebase_uid},
            self.db,
            especialidad_id=self.specialty.id,
            sector="Col. Florencia",
        )
        self.assertEqual(no_match["total"], 0)
        self.assertEqual(no_match["solicitudes"], [])
        self.assertIsNotNone(no_match["mensaje"])
        self.assertIn("filtros", no_match["mensaje"].lower())

        # CA-02: Filtrar por un estado no válido para oportunidades (ej. CANCELADA o COMPLETADA) arroja 400
        with self.assertRaises(HTTPException) as bad_state_err:
            list_available_open_requests_for_technician(
                {"uid": tech.firebase_uid},
                self.db,
                estado="CANCELADA",
            )
        self.assertEqual(bad_state_err.exception.status_code, 400)

    def test_hu27_st02_discard_open_request_hides_only_for_that_technician(self):
        tech_a = User(
            firebase_uid="tech-discard-a",
            correo="ta@example.com",
            nombre_completo="Técnico A",
            dni="0801200000281",
            telefono="+50490000281",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        tech_b = User(
            firebase_uid="tech-discard-b",
            correo="tb@example.com",
            nombre_completo="Técnico B",
            dni="0801200000282",
            telefono="+50490000282",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add_all([tech_a, tech_b])
        self.db.flush()
        for t in (tech_a, tech_b):
            self.db.add(UserRole(usuario_id=t.id, rol="TECNICO"))
            self.db.add(TechnicianProfile(usuario_id=t.id, disponible=True, estado_verificacion="VERIFICADO"))
            self.db.add(TechnicianSpecialty(usuario_id=t.id, especialidad_id=self.specialty.id))
            self.db.add(
                CoverageZone(
                    usuario_id=t.id,
                    departamento="Francisco Morazán",
                    municipio="Distrito Central",
                    sector="Col. Palmira",
                )
            )
        self.db.commit()

        req_1 = publish_open_request(
            self._valid_payload(municipio="Distrito Central", sector="Col. Palmira", presupuesto=Decimal("500.00")),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        req_2 = publish_open_request(
            self._valid_payload(municipio="Distrito Central", sector="Col. Palmira", presupuesto=Decimal("700.00")),
            {"uid": self.client.firebase_uid},
            self.db,
        )

        # Ambos técnicos ven las 2 oportunidades inicialmente
        self.assertEqual(
            list_available_open_requests_for_technician({"uid": tech_a.firebase_uid}, self.db)["total"],
            2,
        )
        self.assertEqual(
            list_available_open_requests_for_technician({"uid": tech_b.firebase_uid}, self.db)["total"],
            2,
        )

        # CA-05: Técnico A descarta req_1 (y es idempotente si vuelve a descartarla)
        discard_res = discard_open_request_for_technician(
            req_1["id"],
            {"uid": tech_a.firebase_uid},
            self.db,
        )
        self.assertTrue(discard_res["descartada"])
        self.assertEqual(discard_res["solicitud_id"], req_1["id"])
        self.assertEqual(discard_res["estado_solicitud"], "PUBLICADA")

        discard_again = discard_open_request_for_technician(
            req_1["id"],
            {"uid": tech_a.firebase_uid},
            self.db,
        )
        self.assertEqual(discard_again["descartada_en"], discard_res["descartada_en"])

        # Se creó exactamente 1 fila en solicitudes_abiertas_descartadas para tech_a
        rows = self.db.query(DiscardedOpenRequest).filter_by(usuario_id=tech_a.id).all()
        self.assertEqual(len(rows), 1)

        # CA-05: Técnico A ya no ve req_1 en su listado personal (solo ve req_2)
        list_a = list_available_open_requests_for_technician({"uid": tech_a.firebase_uid}, self.db)
        self.assertEqual(list_a["total"], 1)
        self.assertEqual(list_a["solicitudes"][0]["id"], req_2["id"])

        # CA-05: La solicitud NO se cancela y Técnico B sigue viendo ambas (req_1 y req_2)
        stored_req_1 = self.db.get(OpenRequest, req_1["id"])
        self.assertEqual(stored_req_1.estado, "Publicada")
        list_b = list_available_open_requests_for_technician({"uid": tech_b.firebase_uid}, self.db)
        self.assertEqual(list_b["total"], 2)
        self.assertEqual({item["id"] for item in list_b["solicitudes"]}, {req_1["id"], req_2["id"]})

    def test_hu27_st04_shared_operational_validation_blocks_bidding_and_repeats_on_action(self):
        tech = User(
            firebase_uid="tech-st04-shared",
            correo="st04@example.com",
            nombre_completo="Técnico Validación Compartida",
            dni="0801200000291",
            telefono="+50490000291",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add(tech)
        self.db.flush()
        self.db.add(UserRole(usuario_id=tech.id, rol="TECNICO"))
        profile = TechnicianProfile(usuario_id=tech.id, disponible=True, estado_verificacion="VERIFICADO")
        self.db.add(profile)
        self.db.add(TechnicianSpecialty(usuario_id=tech.id, especialidad_id=self.specialty.id))
        self.db.add(
            CoverageZone(
                usuario_id=tech.id,
                departamento="Francisco Morazán",
                municipio="Distrito Central",
                sector="Col. Palmira",
            )
        )
        self.db.commit()

        req_new = publish_open_request(
            self._valid_payload(municipio="Distrito Central", sector="Col. Palmira", presupuesto=Decimal("900.00")),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        req_accepted = publish_open_request(
            self._valid_payload(municipio="Distrito Central", sector="Col. Palmira", presupuesto=Decimal("1100.00")),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        accepted_row = self.db.get(OpenRequest, req_accepted["id"])
        accepted_row.estado = "Aceptada"
        accepted_row.tecnico_id = tech.firebase_uid
        self.db.commit()

        # 1. El técnico habilitado carga el listado antes y valida que puede postular a req_new
        loaded_list = list_available_open_requests_for_technician({"uid": tech.firebase_uid}, self.db)
        self.assertEqual(loaded_list["total"], 1)
        can_bid = validate_technician_can_bid_on_open_request(req_new["id"], {"uid": tech.firebase_uid}, self.db)
        self.assertTrue(can_bid["elegible_para_postular"])

        # 2. Aunque el listado se haya cargado antes, si pasa a No disponible, se bloquea postulación directa por API (CA-04)
        profile.disponible = False
        self.db.commit()
        with self.assertRaises(HTTPException) as unav_bid_err:
            validate_technician_can_bid_on_open_request(req_new["id"], {"uid": tech.firebase_uid}, self.db)
        self.assertEqual(unav_bid_err.exception.status_code, 403)

        # Condición transversal: No disponible impide nuevas postulaciones pero NO cancela trabajos ya aceptados
        self.assertEqual(self.db.get(OpenRequest, req_accepted["id"]).estado, "Aceptada")
        accepted_detail = get_open_request_for_viewer(req_accepted["id"], {"uid": tech.firebase_uid}, self.db)
        self.assertEqual(accepted_detail["estado"], "ACEPTADA")

        # 3. Técnico suspendido no puede postular por API (CA-04)
        profile.disponible = True
        profile.suspendido = True
        self.db.commit()
        with self.assertRaises(HTTPException) as susp_bid_err:
            validate_technician_can_bid_on_open_request(req_new["id"], {"uid": tech.firebase_uid}, self.db)
        self.assertEqual(susp_bid_err.exception.status_code, 403)

        # 4. Técnico restringido por saldo no puede postular por API (CA-04)
        profile.suspendido = False
        profile.restriccion_financiera = True
        self.db.commit()
        with self.assertRaises(HTTPException) as fin_bid_err:
            validate_technician_can_bid_on_open_request(req_new["id"], {"uid": tech.firebase_uid}, self.db)
        self.assertEqual(fin_bid_err.exception.status_code, 403)

        # 5. Técnico no verificado no puede postular por API (CA-04)
        profile.restriccion_financiera = False
        profile.estado_verificacion = "RESTRINGIDO"
        self.db.commit()
        with self.assertRaises(HTTPException) as unver_bid_err:
            validate_technician_can_bid_on_open_request(req_new["id"], {"uid": tech.firebase_uid}, self.db)
        self.assertEqual(unver_bid_err.exception.status_code, 403)

    def test_hu27_st05_incompatibilities_private_data_and_independent_discard_matrix(self):
        spec_other = Specialty(nombre="Albañilería")
        tech_1 = User(
            firebase_uid="tech-st05-1",
            correo="st05-1@example.com",
            nombre_completo="Técnico ST05 Uno",
            dni="0801200000301",
            telefono="+50490000301",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        tech_2 = User(
            firebase_uid="tech-st05-2",
            correo="st05-2@example.com",
            nombre_completo="Técnico ST05 Dos",
            dni="0801200000302",
            telefono="+50490000302",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add_all([spec_other, tech_1, tech_2])
        self.db.flush()
        for t in (tech_1, tech_2):
            self.db.add(UserRole(usuario_id=t.id, rol="TECNICO"))
            self.db.add(TechnicianProfile(usuario_id=t.id, disponible=True, estado_verificacion="VERIFICADO"))
            self.db.add(TechnicianSpecialty(usuario_id=t.id, especialidad_id=self.specialty.id))
            self.db.add(
                CoverageZone(
                    usuario_id=t.id,
                    departamento="Francisco Morazán",
                    municipio="Distrito Central",
                    sector="Col. Palmira",
                )
            )
        self.db.commit()

        # Oportunidad compatible en estado "Con ofertas" con dirección exacta y teléfono privados
        req_compatible = publish_open_request(
            self._valid_payload(
                especialidad_id=self.specialty.id,
                municipio="Distrito Central",
                sector="Col. Palmira",
                direccion_exacta="Calle Privada #77, portón café",
                presupuesto=Decimal("980.00"),
            ),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        self.db.get(OpenRequest, req_compatible["id"]).estado = "Con ofertas"

        # Solicitudes incompatibles (otro oficio, otra zona, adjudicada, cancelada, completada)
        req_wrong_trade = publish_open_request(
            self._valid_payload(especialidad_id=spec_other.id, municipio="Distrito Central", sector="Col. Palmira"),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        req_wrong_zone = publish_open_request(
            self._valid_payload(especialidad_id=self.specialty.id, municipio="Distrito Central", sector="Col. Hato de Enmedio"),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        req_adjudicada = publish_open_request(
            self._valid_payload(especialidad_id=self.specialty.id, municipio="Distrito Central", sector="Col. Palmira"),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        self.db.get(OpenRequest, req_adjudicada["id"]).estado = "Adjudicada"
        self.db.get(OpenRequest, req_adjudicada["id"]).tecnico_id = "foreign-tech"
        self.db.commit()

        # 1. Incompatibilidades bloqueadas en listado, detalle, descarte y postulación (CA-02, CA-04)
        for bad_id in (req_wrong_trade["id"], req_wrong_zone["id"], req_adjudicada["id"]):
            with self.subTest(bad_id=bad_id):
                with self.assertRaises(HTTPException) as det_err:
                    get_open_request_for_viewer(bad_id, {"uid": tech_1.firebase_uid}, self.db)
                self.assertEqual(det_err.exception.status_code, 403)

                with self.assertRaises(HTTPException) as disc_err:
                    discard_open_request_for_technician(bad_id, {"uid": tech_1.firebase_uid}, self.db)
                self.assertEqual(disc_err.exception.status_code, 403)

                with self.assertRaises(HTTPException) as bid_err:
                    validate_technician_can_bid_on_open_request(bad_id, {"uid": tech_1.firebase_uid}, self.db)
                self.assertEqual(bid_err.exception.status_code, 403)

        # 2. Datos privados ocultos en listado y detalle de "Con ofertas" antes de Aceptada (CA-03)
        listing_1 = list_available_open_requests_for_technician({"uid": tech_1.firebase_uid}, self.db)
        self.assertEqual(listing_1["total"], 1)
        card = listing_1["solicitudes"][0]
        self.assertEqual(card["id"], req_compatible["id"])
        self.assertEqual(card["presupuesto"], 980.0)
        self.assertEqual(card["municipio"], "Distrito Central")
        self.assertEqual(card["sector"], "Col. Palmira")
        self.assertNotIn("direccion_exacta", card)
        self.assertNotIn("telefono", card["cliente"])

        detail_1 = get_open_request_for_viewer(req_compatible["id"], {"uid": tech_1.firebase_uid}, self.db)
        self.assertNotIn("direccion_exacta", detail_1)
        self.assertNotIn("telefono", detail_1["cliente"])

        # 3. Descarte independiente (CA-05): tech_1 descarta req_compatible; sigue Con ofertas para el cliente y visible para tech_2
        discard_open_request_for_technician(req_compatible["id"], {"uid": tech_1.firebase_uid}, self.db)
        after_discard_1 = list_available_open_requests_for_technician({"uid": tech_1.firebase_uid}, self.db)
        self.assertEqual(after_discard_1["total"], 0)
        self.assertIsNotNone(after_discard_1["mensaje"])

        after_discard_2 = list_available_open_requests_for_technician({"uid": tech_2.firebase_uid}, self.db)
        self.assertEqual(after_discard_2["total"], 1)
        self.assertEqual(after_discard_2["solicitudes"][0]["id"], req_compatible["id"])
        self.assertEqual(self.db.get(OpenRequest, req_compatible["id"]).estado, "Con ofertas")

    def test_hu28_st01_proposal_model_and_unique_active_proposal_per_technician_and_request(self):
        tech = User(
            firebase_uid="tech-hu28-st01",
            correo="hu28-st01@example.com",
            nombre_completo="Técnico Propuesta",
            dni="0801200000401",
            telefono="+50490000401",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add(tech)
        self.db.flush()
        self.db.add(UserRole(usuario_id=tech.id, rol="TECNICO"))
        self.db.add(TechnicianProfile(usuario_id=tech.id, disponible=True, estado_verificacion="VERIFICADO"))
        self.db.commit()

        published = publish_open_request(
            self._valid_payload(presupuesto=Decimal("850.00")),
            {"uid": self.client.firebase_uid},
            self.db,
        )

        # CA-02: La propuesta conserva técnico, monto, fecha y estado PENDIENTE sin adjudicar el trabajo
        proposal = OpenRequestProposal(
            solicitud_id=published["id"],
            usuario_id=tech.id,
            tecnico_id=tech.firebase_uid,
            tipo="ACEPTACION_PRESUPUESTO",
            monto=Decimal("850.00"),
            estado="PENDIENTE",
            activa=True,
        )
        self.db.add(proposal)
        self.db.commit()
        self.db.refresh(proposal)

        self.assertEqual(proposal.solicitud_id, published["id"])
        self.assertEqual(proposal.usuario_id, tech.id)
        self.assertEqual(proposal.tecnico_id, tech.firebase_uid)
        self.assertEqual(Decimal(str(proposal.monto)), Decimal("850.00"))
        self.assertEqual(proposal.estado, "PENDIENTE")
        self.assertTrue(proposal.activa)
        self.assertIsNotNone(proposal.creada_en)
        self.assertIsNone(self.db.get(OpenRequest, published["id"]).tecnico_id)

        # CA-04: Restricción de base de datos impide duplicar propuestas del mismo técnico en la misma licitación
        duplicate = OpenRequestProposal(
            solicitud_id=published["id"],
            usuario_id=tech.id,
            tecnico_id=tech.firebase_uid,
            tipo="ACEPTACION_PRESUPUESTO",
            monto=Decimal("850.00"),
            estado="PENDIENTE",
            activa=True,
        )
        self.db.add(duplicate)
        with self.assertRaises(IntegrityError):
            self.db.commit()
        self.db.rollback()

    def test_hu28_st02_accept_published_budget_state_transitions_and_idempotency(self):
        tech_1 = User(
            firebase_uid="tech-hu28-st02-1",
            correo="hu28-st02-1@example.com",
            nombre_completo="Técnico Uno ST02",
            dni="0801200000402",
            telefono="+50490000402",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        tech_2 = User(
            firebase_uid="tech-hu28-st02-2",
            correo="hu28-st02-2@example.com",
            nombre_completo="Técnico Dos ST02",
            dni="0801200000403",
            telefono="+50490000403",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add_all([tech_1, tech_2])
        self.db.flush()
        for t in (tech_1, tech_2):
            self.db.add(UserRole(usuario_id=t.id, rol="TECNICO"))
            self.db.add(TechnicianProfile(usuario_id=t.id, disponible=True, estado_verificacion="VERIFICADO"))
            self.db.add(TechnicianSpecialty(usuario_id=t.id, especialidad_id=self.specialty.id))
            self.db.add(
                CoverageZone(
                    usuario_id=t.id,
                    departamento="Francisco Morazán",
                    municipio="Distrito Central",
                    sector="Col. Palmira",
                )
            )
        self.db.commit()

        published = publish_open_request(
            self._valid_payload(
                municipio="Distrito Central",
                sector="Col. Palmira",
                presupuesto=Decimal("780.00"),
            ),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        req_id = published["id"]

        # CA-01, CA-02, CA-03: Primera propuesta por el monto publicado cambia Publicada -> Con ofertas sin adjudicar
        res_1 = accept_published_budget_for_open_request(
            req_id,
            {"uid": tech_1.firebase_uid},
            self.db,
        )
        self.assertFalse(res_1["reintento_idempotente"])
        self.assertEqual(res_1["propuesta"]["monto"], 780.0)
        self.assertEqual(res_1["propuesta"]["estado"], "PENDIENTE")
        self.assertEqual(res_1["estado_solicitud"], "CON OFERTAS")
        self.assertIsNone(self.db.get(OpenRequest, req_id).tecnico_id)
        self.assertEqual(self.db.get(OpenRequest, req_id).estado, "Con ofertas")

        # CA-04: Reintento de la misma aceptación por tech_1 no crea duplicados y devuelve reintento_idempotente=True
        retry_1 = accept_published_budget_for_open_request(
            req_id,
            {"uid": tech_1.firebase_uid},
            self.db,
        )
        self.assertTrue(retry_1["reintento_idempotente"])
        self.assertEqual(retry_1["propuesta"]["id"], res_1["propuesta"]["id"])
        self.assertEqual(
            self.db.query(OpenRequestProposal).filter_by(solicitud_id=req_id, usuario_id=tech_1.id).count(),
            1,
        )

        # CA-03: Segunda propuesta (de tech_2) mantiene el estado en "Con ofertas"
        res_2 = accept_published_budget_for_open_request(
            req_id,
            {"uid": tech_2.firebase_uid},
            self.db,
        )
        self.assertFalse(res_2["reintento_idempotente"])
        self.assertEqual(res_2["estado_solicitud"], "CON OFERTAS")
        self.assertEqual(self.db.get(OpenRequest, req_id).estado, "Con ofertas")
        self.assertEqual(
            self.db.query(OpenRequestProposal).filter_by(solicitud_id=req_id).count(),
            2,
        )

        # CA-06: Si la solicitud ya fue adjudicada a otro técnico, rechaza sin modificar la contratación
        req_row = self.db.get(OpenRequest, req_id)
        req_row.estado = "Aceptada"
        req_row.tecnico_id = tech_1.firebase_uid
        self.db.commit()

        with self.assertRaises(HTTPException) as adj_err:
            accept_published_budget_for_open_request(
                req_id,
                {"uid": tech_2.firebase_uid},
                self.db,
            )
        self.assertEqual(adj_err.exception.status_code, 409)
        self.assertEqual(self.db.get(OpenRequest, req_id).tecnico_id, tech_1.firebase_uid)
        self.assertEqual(self.db.get(OpenRequest, req_id).estado, "Aceptada")

    def test_hu28_st04_emit_new_offer_event_and_client_views_proposal_while_privacy_remains(self):
        tech = User(
            firebase_uid="tech-hu28-st04",
            correo="hu28-st04@example.com",
            nombre_completo="Técnico Evento Oferta",
            dni="0801200000404",
            telefono="+50490000404",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add(tech)
        self.db.flush()
        self.db.add(UserRole(usuario_id=tech.id, rol="TECNICO"))
        self.db.add(TechnicianProfile(usuario_id=tech.id, disponible=True, estado_verificacion="VERIFICADO"))
        self.db.add(TechnicianSpecialty(usuario_id=tech.id, especialidad_id=self.specialty.id))
        self.db.add(
            CoverageZone(
                usuario_id=tech.id,
                departamento="Francisco Morazán",
                municipio="Distrito Central",
                sector="Col. Palmira",
            )
        )
        self.db.commit()

        published = publish_open_request(
            self._valid_payload(
                municipio="Distrito Central",
                sector="Col. Palmira",
                direccion_exacta="Boulevard Morazán, Edificio 10, piso 3",
                presupuesto=Decimal("920.00"),
            ),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        req_id = published["id"]

        accepted = accept_published_budget_for_open_request(
            req_id,
            {"uid": tech.firebase_uid},
            self.db,
        )

        # CA-05: Se emite evento NUEVA_OFERTA y no expone dirección exacta ni teléfono del cliente
        self.assertIn("evento", accepted)
        event = accepted["evento"]
        self.assertEqual(event["tipo"], "NUEVA_OFERTA")
        self.assertEqual(event["solicitud_id"], req_id)
        self.assertEqual(event["cliente_id"], self.client.firebase_uid)
        self.assertTrue(event["ubicacion_exacta_protegida"])
        self.assertNotIn("direccion_exacta", event)
        self.assertNotIn("telefono", str(event))
        self.assertNotIn("direccion_exacta", accepted["solicitud"])
        self.assertNotIn("telefono", accepted["solicitud"]["cliente"])

        # CA-05: El cliente puede ver la propuesta y el evento tanto en el detalle de solicitud abierta como en /users/me/requests/{id}
        owner_feed = list_open_request_proposals_for_viewer(
            req_id,
            {"uid": self.client.firebase_uid},
            self.db,
        )
        self.assertEqual(owner_feed["total"], 1)
        self.assertEqual(owner_feed["propuestas"][0]["monto"], 920.0)
        self.assertEqual(owner_feed["propuestas"][0]["tecnico"]["nombre_completo"], "Técnico Evento Oferta")
        self.assertEqual(len(owner_feed["eventos"]), 1)

        client_detail = get_client_request_detail(
            req_id,
            {"uid": self.client.firebase_uid},
            self.db,
        )
        self.assertEqual(client_detail["estado"], "CON OFERTAS")
        self.assertEqual(len(client_detail["propuestas_recibidas"]), 1)
        self.assertEqual(client_detail["propuestas_recibidas"][0]["monto"], 920.0)

    def test_hu28_st05_retries_multiple_technicians_self_competition_and_concurrent_closing(self):
        techs: list[User] = []
        for idx in range(1, 5):
            u = User(
                firebase_uid=f"tech-hu28-st05-{idx}",
                correo=f"hu28-st05-{idx}@example.com",
                nombre_completo=f"Técnico Concurrente {idx}",
                dni=f"080120000050{idx}",
                telefono=f"+5049000050{idx}",
                telefono_verificado=True,
                estado_identidad="VERIFICADO",
            )
            techs.append(u)
        self.db.add_all(techs)
        self.db.flush()
        for u in techs:
            self.db.add(UserRole(usuario_id=u.id, rol="TECNICO"))
            self.db.add(TechnicianProfile(usuario_id=u.id, disponible=True, estado_verificacion="VERIFICADO"))
            self.db.add(TechnicianSpecialty(usuario_id=u.id, especialidad_id=self.specialty.id))
            self.db.add(
                CoverageZone(
                    usuario_id=u.id,
                    departamento="Francisco Morazán",
                    municipio="Distrito Central",
                    sector="Col. Palmira",
                )
            )
        self.db.commit()

        published = publish_open_request(
            self._valid_payload(
                municipio="Distrito Central",
                sector="Col. Palmira",
                direccion_exacta="Residencial Las Uvas, bloque M, casa 14",
                presupuesto=Decimal("1050.00"),
            ),
            {"uid": self.client.firebase_uid},
            self.db,
        )
        req_id = published["id"]

        # 1. Varios técnicos (tech_1 y tech_2) aceptan el monto publicado; primero pasa a Con ofertas y luego mantiene Con ofertas (CA-02, CA-03)
        resp_1 = Response()
        first = accept_published_budget_endpoint(
            solicitud_id=req_id,
            response=resp_1,
            claims={"uid": techs[0].firebase_uid},
            db=self.db,
        )
        self.assertFalse(first["reintento_idempotente"])
        self.assertEqual(first["estado_solicitud"], "CON OFERTAS")

        resp_2 = Response()
        second = accept_published_budget_endpoint(
            solicitud_id=req_id,
            response=resp_2,
            claims={"uid": techs[1].firebase_uid},
            db=self.db,
        )
        self.assertFalse(second["reintento_idempotente"])
        self.assertEqual(second["estado_solicitud"], "CON OFERTAS")
        self.assertIsNone(self.db.get(OpenRequest, req_id).tecnico_id)

        # 2. Reintentos repetidos del mismo técnico (tech_1) devuelven 200 OK sin crear propuestas ni eventos duplicados (CA-04)
        for _ in range(2):
            retry_resp = Response()
            retry = accept_published_budget_endpoint(
                solicitud_id=req_id,
                response=retry_resp,
                claims={"uid": techs[0].firebase_uid},
                db=self.db,
            )
            self.assertEqual(retry_resp.status_code, 200)
            self.assertTrue(retry["reintento_idempotente"])
            self.assertEqual(retry["propuesta"]["id"], first["propuesta"]["id"])

        owner_feed = list_open_request_proposals_for_viewer(
            req_id,
            {"uid": self.client.firebase_uid},
            self.db,
        )
        self.assertEqual(owner_feed["total"], 2)
        self.assertEqual(len(owner_feed["eventos"]), 2)

        # Cada técnico ve únicamente su propia propuesta y sin acceso a dirección exacta ni teléfono (CA-05)
        tech_1_feed = list_open_request_proposals_for_viewer(
            req_id,
            {"uid": techs[0].firebase_uid},
            self.db,
        )
        self.assertEqual(tech_1_feed["total"], 1)
        self.assertEqual(tech_1_feed["propuestas"][0]["id"], first["propuesta"]["id"])
        self.assertTrue(tech_1_feed["ubicacion_exacta_protegida"])

        # 3. No se permite competir contra sí mismo si ya tiene una propuesta activa de otro tipo (ej. CONTRAOFERTA) (CA-04)
        self.db.add(
            OpenRequestProposal(
                solicitud_id=req_id,
                usuario_id=techs[2].id,
                tecnico_id=techs[2].firebase_uid,
                tipo="CONTRAOFERTA",
                monto=Decimal("1300.00"),
                justificacion="Incluye materiales adicionales",
                estado="PENDIENTE",
                activa=True,
            )
        )
        self.db.commit()
        with self.assertRaises(HTTPException) as self_comp_err:
            accept_published_budget_for_open_request(
                req_id,
                {"uid": techs[2].firebase_uid},
                self.db,
            )
        self.assertEqual(self_comp_err.exception.status_code, 409)

        # 4. Cierre / adjudicación concurrente antes de que tech_4 confirme su postulación (CA-06)
        req_row = self.db.get(OpenRequest, req_id)
        req_row.estado = "Aceptada"
        req_row.tecnico_id = techs[0].firebase_uid
        self.db.commit()

        with self.assertRaises(HTTPException) as concurrent_close_err:
            accept_published_budget_for_open_request(
                req_id,
                {"uid": techs[3].firebase_uid},
                self.db,
            )
        self.assertEqual(concurrent_close_err.exception.status_code, 409)
        self.assertIn("Otro técnico ya fue adjudicado", concurrent_close_err.exception.detail)
        # No se modificó la contratación ni se agregó propuesta de tech_4
        self.assertEqual(self.db.get(OpenRequest, req_id).tecnico_id, techs[0].firebase_uid)
        self.assertEqual(self.db.get(OpenRequest, req_id).estado, "Aceptada")
        self.assertEqual(
            self.db.query(OpenRequestProposal).filter_by(solicitud_id=req_id, usuario_id=techs[3].id).count(),
            0,
        )

    def test_honduras_location_equivalence_and_coverage_catalog(self):
        tech = User(
            firebase_uid="tech-hn-loc",
            correo="hnloc@example.com",
            nombre_completo="Técnico Honduras",
            dni="0801200000888",
            telefono="+50498888888",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add(tech)
        self.db.flush()
        self.db.add(UserRole(usuario_id=tech.id, rol="TECNICO"))
        self.db.add(TechnicianProfile(usuario_id=tech.id, disponible=True, estado_verificacion="VERIFICADO"))
        self.db.add(TechnicianSpecialty(usuario_id=tech.id, especialidad_id=self.specialty.id))
        # El técnico seleccionó desde CoverageZoneSelector (hondurasLocation.json):
        # Municipio: "Distrito Central (Tegucigalpa)" y Sector: "Kennedy"
        self.db.add(
            CoverageZone(
                usuario_id=tech.id,
                departamento="Francisco Morazán",
                municipio="Distrito Central (Tegucigalpa)",
                sector="Kennedy",
            )
        )
        self.db.commit()

        # El cliente publica con "Distrito Central" y "Col. Kennedy"
        req = publish_open_request(
            self._valid_payload(
                especialidad_id=self.specialty.id,
                municipio="Distrito Central",
                sector="Col. Kennedy",
                presupuesto=Decimal("900.00"),
            ),
            {"uid": self.client.firebase_uid},
            self.db,
        )

        # La solicitud abierta aparece en las oportunidades disponibles del técnico
        available = list_available_open_requests_for_technician({"uid": tech.firebase_uid}, self.db)
        self.assertEqual(available["total"], 1)
        self.assertEqual(available["solicitudes"][0]["id"], req["id"])

        # El catálogo de zonas de cobertura devuelve la zona registrada
        catalog = list_coverage_zones_catalog(self.db, municipio="Tegucigalpa")
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0]["sector"], "Kennedy")
