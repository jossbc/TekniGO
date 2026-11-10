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
    DiscardedOpenRequest,
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
    discard_open_request_for_technician,
    get_open_request_for_viewer,
    get_suggested_range_endpoint,
    list_available_open_requests_for_technician,
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
