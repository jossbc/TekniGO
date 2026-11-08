from datetime import date, timedelta
import unittest
import uuid

from fastapi import HTTPException
from sqlalchemy import create_engine, select
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
from app.technician_activation import (
    REQUIRED_TECHNICIAN_DOCS,
    compute_document_expiration,
    require_operational_technician,
    sync_technician_verification_state,
)
from app.technicians import (
    AdminVerificationReviewSchema,
    DocumentReviewItem,
    JobApplicationRequest,
    accept_service_request,
    apply_to_job_request,
    get_my_activation_status,
    get_technician_public_profile,
    get_technicians_catalog,
    review_technician_verification,
)
from app.users import ProfileUpdate, update_me
import itertools
from app.technician_activation import run_scheduled_expiration_review
from app.technicians import run_expiration_review_admin


class TechnicianActivationServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        self.admin = User(
            firebase_uid="admin-uid",
            nombre_completo="Admin TekniGO",
            correo="admin@example.com",
            dni="0801199000001",
            telefono="+50491111111",
            telefono_verificado=True,
        )
        self.db.add(self.admin)
        self.db.flush()
        self.db.add(UserRole(usuario_id=self.admin.id, rol="ADMIN"))

        self.tech_user = User(
            firebase_uid="tech-uid",
            nombre_completo="Mario Electricista",
            correo="mario@example.com",
            dni="0801199500002",
            telefono="+50492222222",
            telefono_verificado=True,
        )
        self.db.add(self.tech_user)
        self.db.flush()
        self.db.add(UserRole(usuario_id=self.tech_user.id, rol="TECNICO"))
        self.profile = TechnicianProfile(
            usuario_id=self.tech_user.id,
            disponible=True,
            estado_verificacion="EN_REVISION",
        )
        self.db.add(self.profile)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _seed_documents(
        self,
        *,
        state: str = "APROBADO",
        emision: date | None = None,
        omit_type: str | None = None,
    ) -> dict[str, IdentityDocument]:
        emision_date = emision or date.today()
        req = IdentityVerificationRequest(usuario_id=self.tech_user.id, version=1)
        self.db.add(req)
        self.db.flush()

        docs: dict[str, IdentityDocument] = {}
        for idx, (doc_type, _) in enumerate(REQUIRED_TECHNICIAN_DOCS):
            if doc_type == omit_type:
                continue
            is_antecedente = doc_type in ("ANTECEDENTES_POLICIALES", "ANTECEDENTES_PENALES")
            fecha_emision = emision_date if is_antecedente else None
            doc = IdentityDocument(
                usuario_id=self.tech_user.id,
                solicitud_id=req.id,
                tipo=doc_type,
                ruta_privada=f"identidad/{self.tech_user.id}/{idx}-{doc_type}.enc",
                content_type="application/pdf" if is_antecedente else "image/jpeg",
                tamano_bytes=1024,
                sha256=f"{idx + 1}" * 64,
                estado=state,
                fecha_emision_declarada=fecha_emision,
                fecha_vencimiento=compute_document_expiration(doc_type, fecha_emision),
            )
            self.db.add(doc)
            docs[doc_type] = doc
        self.db.commit()
        return docs

    def test_ca01_transitions_from_en_revision_to_verificado_only_when_all_docs_and_phone_valid(self):
        docs = self._seed_documents(state="PENDIENTE")
        review_items = [
            DocumentReviewItem(documento_id=doc.id, estado="APROBADO", legible=True, identidad_coincide=True)
            for doc in docs.values()
        ]
        res = review_technician_verification(
            self.tech_user.id,
            AdminVerificationReviewSchema(
                estado_verificacion="VERIFICADO",
                observaciones_verificacion="Expediente completo y verificado.",
                documentos=review_items,
            ),
            {"uid": "admin-uid"},
            self.db,
        )
        self.assertEqual(res["estado_verificacion"], "VERIFICADO")
        self.assertTrue(res["puede_recibir_contrataciones"])
        self.assertEqual(res["requisitos_pendientes"], [])
        self.assertIsNotNone(self.profile.verificado_en)
        self.assertEqual(self.tech_user.estado_identidad, "VERIFICADO")

    def test_ca02_missing_any_approval_or_unverified_phone_keeps_technician_restricted(self):
        docs = self._seed_documents(state="APROBADO")
        # Falta validar teléfono aunque todos los documentos estén aprobados
        self.tech_user.telefono_verificado = False
        self.db.commit()

        evaluation = sync_technician_verification_state(
            self.tech_user,
            self.profile,
            self.db,
            proceso_responsable="PRUEBA_TELEFONO_PENDIENTE",
        )
        self.db.commit()
        self.assertNotEqual(evaluation["estado_verificacion"], "VERIFICADO")
        self.assertFalse(evaluation["puede_recibir_contrataciones"])
        with self.assertRaises(HTTPException) as err:
            require_operational_technician(self.tech_user, self.db)
        self.assertEqual(err.exception.status_code, 403)

        # Teléfono validado pero falta aprobar antecedentes penales
        self.tech_user.telefono_verificado = True
        docs["ANTECEDENTES_PENALES"].estado = "PENDIENTE"
        self.db.commit()

        evaluation_missing_doc = sync_technician_verification_state(
            self.tech_user,
            self.profile,
            self.db,
            proceso_responsable="PRUEBA_DOC_PENDIENTE",
        )
        self.db.commit()
        self.assertEqual(evaluation_missing_doc["estado_verificacion"], "EN_REVISION")
        self.assertFalse(evaluation_missing_doc["puede_recibir_contrataciones"])
        with self.assertRaises(HTTPException) as err2:
            require_operational_technician(self.tech_user, self.db)
        self.assertEqual(err2.exception.status_code, 403)

    def test_ca03_activation_status_separates_verification_from_certification_and_rating_and_hides_private_data(self):
        self._seed_documents(state="APROBADO")
        self.profile.estado_certificacion = "CERTIFICADO_INFOP"
        self.profile.calificacion_promedio = 4.85
        self.db.commit()

        status = get_my_activation_status({"uid": "tech-uid"}, self.db)
        self.assertTrue(status["tecnico_verificado"])
        self.assertEqual(status["estado_verificacion"], "VERIFICADO")
        self.assertEqual(status["estado_certificacion"], "CERTIFICADO_INFOP")
        self.assertAlmostEqual(status["calificacion_promedio"], 4.85, places=2)
        for req in status["requisitos"]:
            self.assertNotIn("ruta_privada", req)
            self.assertNotIn("sha256", req)

    def test_ca04_expired_requirement_or_phone_change_withdraws_enablement_and_informs_reason(self):
        docs = self._seed_documents(state="APROBADO")
        sync_technician_verification_state(
            self.tech_user,
            self.profile,
            self.db,
            proceso_responsable="ACTIVACION_INICIAL",
        )
        self.db.commit()
        self.assertEqual(self.profile.estado_verificacion, "VERIFICADO")

        # 1. Vence antecedente policial
        docs["ANTECEDENTES_POLICIALES"].fecha_vencimiento = date.today() - timedelta(days=1)
        self.db.commit()

        expired_eval = sync_technician_verification_state(
            self.tech_user,
            self.profile,
            self.db,
            proceso_responsable="REVISION_VENCIMIENTOS",
        )
        self.db.commit()
        self.assertEqual(expired_eval["estado_verificacion"], "RESTRINGIDO")
        self.assertFalse(expired_eval["puede_recibir_contrataciones"])
        self.assertIn("Antecedentes policiales", expired_eval["motivo_restriccion"])
        self.assertEqual(docs["ANTECEDENTES_POLICIALES"].estado, "VENCIDO")

        # Renovamos el documento y vuelve a VERIFICADO
        docs["ANTECEDENTES_POLICIALES"].estado = "APROBADO"
        docs["ANTECEDENTES_POLICIALES"].fecha_vencimiento = date.today() + timedelta(days=60)
        sync_technician_verification_state(
            self.tech_user,
            self.profile,
            self.db,
            proceso_responsable="RENOVACION_DOCUMENTAL",
        )
        self.db.commit()
        self.assertEqual(self.profile.estado_verificacion, "VERIFICADO")

        # 2. Cambio de teléfono invalida la verificación previa y retira habilitación
        update_me(ProfileUpdate(telefono="9888-7766"), {"uid": "tech-uid"}, self.db)
        self.db.refresh(self.tech_user)
        self.db.refresh(self.profile)
        self.assertFalse(self.tech_user.telefono_verificado)
        self.assertEqual(self.profile.estado_verificacion, "RESTRINGIDO")
        self.assertIn("teléfono", (self.profile.motivo_restriccion or "").lower())

    def test_ca05_document_approval_does_not_clear_suspensions_unavailability_or_financial_restrictions(self):
        self._seed_documents(state="APROBADO")
        self.profile.disponible = False
        self.profile.suspendido = True
        self.profile.restriccion_financiera = True
        self.db.commit()

        evaluation = sync_technician_verification_state(
            self.tech_user,
            self.profile,
            self.db,
            proceso_responsable="REVISION_DOCUMENTAL_ADMIN",
            actor_usuario_id=self.admin.id,
        )
        self.db.commit()

        # Queda documentalmente VERIFICADO, pero conserva sus restricciones y NO queda habilitado operativamente
        self.assertEqual(evaluation["estado_verificacion"], "VERIFICADO")
        self.assertFalse(self.profile.disponible)
        self.assertTrue(self.profile.suspendido)
        self.assertTrue(self.profile.restriccion_financiera)
        self.assertFalse(evaluation["puede_recibir_contrataciones"])
        self.assertEqual(len(evaluation["motivos_bloqueo"]), 3)

    def test_ca06_transitions_record_date_cause_and_responsible_actor_or_process(self):
        self._seed_documents(state="APROBADO")
        sync_technician_verification_state(
            self.tech_user,
            self.profile,
            self.db,
            proceso_responsable="REVISION_DOCUMENTAL_ADMIN",
            actor_usuario_id=self.admin.id,
        )
        self.db.commit()

        audits = self.db.scalars(
            select(TechnicianStatusAudit)
            .where(TechnicianStatusAudit.usuario_id == self.tech_user.id)
            .order_by(TechnicianStatusAudit.creado_en.asc())
        ).all()
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].estado_anterior, "EN_REVISION")
        self.assertEqual(audits[0].estado_nuevo, "VERIFICADO")
        self.assertFalse(audits[0].habilitado_anterior)
        self.assertTrue(audits[0].habilitado_nuevo)
        self.assertEqual(audits[0].actor_usuario_id, self.admin.id)
        self.assertEqual(audits[0].proceso_responsable, "REVISION_DOCUMENTAL_ADMIN")
        self.assertIsNotNone(audits[0].creado_en)
        self.assertIn("Verificado", audits[0].causa)

    # ---- HU-18 ST-03: Catálogo, perfil público, solicitudes y postulaciones ----

    def test_st03_catalog_and_public_profile_reflect_status_changes_and_hide_identity_data(self):
        docs = self._seed_documents(state="APROBADO")
        self.profile.estado_certificacion = "CERTIFICADO_TECNICO"
        self.profile.calificacion_promedio = 4.9
        sync_technician_verification_state(
            self.tech_user,
            self.profile,
            self.db,
            proceso_responsable="ACTIVACION_INICIAL",
        )
        self.db.commit()

        # 1. Técnico verificado y disponible aparece en catálogo y perfil público (CA-01, CA-03, CA-06)
        catalog = get_technicians_catalog(nombre=None, especialidad=None, municipio=None, db=self.db)
        self.assertEqual(len(catalog), 1)
        item = catalog[0]
        self.assertEqual(item["usuario_id"], str(self.tech_user.id))
        self.assertTrue(item["tecnico_verificado"])
        self.assertEqual(item["estado_verificacion"], "VERIFICADO")
        self.assertEqual(item["estado_certificacion"], "CERTIFICADO_TECNICO")
        self.assertAlmostEqual(item["calificacion_promedio"], 4.9, places=2)
        self.assertNotIn("dni", item)
        self.assertNotIn("telefono", item)
        self.assertNotIn("documentos", item)

        public_profile = get_technician_public_profile(self.tech_user.id, self.db)
        self.assertTrue(public_profile["tecnico_verificado"])
        self.assertEqual(public_profile["estado_certificacion"], "CERTIFICADO_TECNICO")
        self.assertNotIn("dni", public_profile)
        self.assertNotIn("telefono", public_profile)
        self.assertNotIn("documentos", public_profile)

        # 2. Al vencer un antecedente y volver a consultar el catálogo, se retira habilitación (CA-04, CA-06)
        docs["ANTECEDENTES_PENALES"].fecha_vencimiento = date.today() - timedelta(days=2)
        self.db.commit()

        catalog_after_expiry = get_technicians_catalog(nombre=None, especialidad=None, municipio=None, db=self.db)
        self.assertEqual(catalog_after_expiry, [])
        self.db.refresh(self.profile)
        self.assertEqual(self.profile.estado_verificacion, "RESTRINGIDO")

        with self.assertRaises(HTTPException) as not_avail:
            get_technician_public_profile(self.tech_user.id, self.db)
        self.assertEqual(not_avail.exception.status_code, 404)

    def test_st03_requests_and_applications_api_recheck_operational_permissions(self):
        docs = self._seed_documents(state="APROBADO")
        sync_technician_verification_state(
            self.tech_user,
            self.profile,
            self.db,
            proceso_responsable="ACTIVACION_INICIAL",
        )
        self.db.commit()

        req_id = uuid.uuid4()
        accepted = accept_service_request(req_id, {"uid": "tech-uid"}, self.db)
        self.assertEqual(accepted["status"], "accepted")
        self.assertTrue(accepted["habilitado_operativamente"])

        applied = apply_to_job_request(
            JobApplicationRequest(solicitud_id=req_id, mensaje="Disponible hoy"),
            {"uid": "tech-uid"},
            self.db,
        )
        self.assertEqual(applied["status"], "applied")

        # Si el técnico es suspendido, tiene restricción financiera, pasa a no disponible o vence un documento,
        # la API repite la verificación al ejecutar la acción y rechaza con 403 (CA-02, CA-05).
        for field, val in (
            ("suspendido", True),
            ("restriccion_financiera", True),
            ("disponible", False),
        ):
            with self.subTest(restriction=field):
                setattr(self.profile, field, val)
                self.db.commit()
                with self.assertRaises(HTTPException) as err_accept:
                    accept_service_request(req_id, {"uid": "tech-uid"}, self.db)
                self.assertEqual(err_accept.exception.status_code, 403)

                with self.assertRaises(HTTPException) as err_apply:
                    apply_to_job_request(
                        JobApplicationRequest(solicitud_id=req_id),
                        {"uid": "tech-uid"},
                        self.db,
                    )
                self.assertEqual(err_apply.exception.status_code, 403)
                # Restaurar valor
                setattr(self.profile, field, not val)
                self.db.commit()

        # Vence un documento justo antes de aceptar una solicitud aunque antes aparecía habilitado
        docs["ANTECEDENTES_POLICIALES"].fecha_vencimiento = date.today() - timedelta(days=1)
        self.db.commit()
        with self.assertRaises(HTTPException) as err_expired:
            accept_service_request(req_id, {"uid": "tech-uid"}, self.db)
        self.assertEqual(err_expired.exception.status_code, 403)
        self.db.refresh(self.profile)
        self.assertEqual(self.profile.estado_verificacion, "RESTRINGIDO")

    def test_st05_scheduled_expiration_review_restricts_expired_technicians_and_is_idempotent(self):
        docs = self._seed_documents(state="APROBADO")
        sync_technician_verification_state(
            self.tech_user,
            self.profile,
            self.db,
            proceso_responsable="ACTIVACION_INICIAL",
        )
        self.db.commit()
        self.assertEqual(self.profile.estado_verificacion, "VERIFICADO")

        # Segunda cuenta técnica que permanece vigente
        valid_tech_id = uuid.uuid4()
        valid_user = User(
            id=valid_tech_id,
            firebase_uid="tech-valid-uid",
            nombre_completo="Ana Vigente",
            correo="ana@example.com",
            dni="0801199212345",
            telefono="+50497776666",
            telefono_verificado=True,
        )
        valid_profile = TechnicianProfile(
            usuario_id=valid_tech_id,
            disponible=True,
            estado_verificacion="VERIFICADO",
        )
        req_id = uuid.uuid4()
        self.db.add_all(
            [
                valid_user,
                UserRole(usuario_id=valid_tech_id, rol="TECNICO"),
                valid_profile,
                IdentityVerificationRequest(id=req_id, usuario_id=valid_tech_id, version=1),
            ]
        )
        self.db.flush()
        emision = date.today() - timedelta(days=5)
        for idx, (doc_type, _) in enumerate(REQUIRED_TECHNICIAN_DOCS):
            f_emision = emision if "ANTECEDENTES" in doc_type else None
            self.db.add(
                IdentityDocument(
                    id=uuid.uuid4(),
                    usuario_id=valid_tech_id,
                    solicitud_id=req_id,
                    tipo=doc_type,
                    ruta_privada=f"valid-{idx}.enc",
                    content_type="image/jpeg",
                    tamano_bytes=512,
                    sha256=f"{idx + 10:064x}",
                    estado="APROBADO",
                    fecha_emision_declarada=f_emision,
                    fecha_vencimiento=compute_document_expiration(doc_type, f_emision),
                )
            )
        self.db.commit()

        # Simular que vencen los antecedentes policiales del primer técnico
        docs["ANTECEDENTES_POLICIALES"].fecha_emision_declarada = date.today() - timedelta(days=95)
        docs["ANTECEDENTES_POLICIALES"].fecha_vencimiento = date.today() - timedelta(days=5)
        self.db.commit()

        # Ejecutar revisión programada mediante el endpoint administrativo (CA-04, CA-06)
        summary = run_expiration_review_admin(claims={"uid": "admin-uid"}, db=self.db)
        self.assertEqual(summary["total_evaluados"], 2)
        self.assertEqual(summary["total_restringidos_por_vencimiento"], 1)
        self.assertEqual(len(summary["tecnicos_afectados"]), 1)
        self.assertEqual(summary["tecnicos_afectados"][0]["usuario_id"], str(self.tech_user.id))
        self.assertEqual(summary["tecnicos_afectados"][0]["estado_anterior"], "VERIFICADO")
        self.assertEqual(summary["tecnicos_afectados"][0]["estado_nuevo"], "RESTRINGIDO")

        self.db.refresh(self.profile)
        self.db.refresh(valid_profile)
        self.assertEqual(self.profile.estado_verificacion, "RESTRINGIDO")
        self.assertEqual(valid_profile.estado_verificacion, "VERIFICADO")

        # El catálogo solo devuelve al técnico vigente (CA-06)
        catalog = get_technicians_catalog(nombre=None, especialidad=None, municipio=None, db=self.db)
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0]["usuario_id"], str(valid_tech_id))

        # Si vuelve a correr la revisión programada el mismo día sin nuevos cambios, es idempotente
        second_run = run_scheduled_expiration_review(self.db)
        self.assertEqual(second_run["total_restringidos_por_vencimiento"], 0)
        self.assertEqual(second_run["tecnicos_afectados"], [])

    def test_st05_exhaustive_combinations_of_restrictions_matrix(self):
        docs = self._seed_documents(state="APROBADO")
        req_id = uuid.uuid4()

        # Probar las 32 combinaciones booleanas de:
        # (docs_vigentes, telefono_verificado, disponible, suspendido, restriccion_financiera)
        for docs_ok, phone_ok, disp, susp, fin_rest in itertools.product([True, False], repeat=5):
            with self.subTest(
                docs_ok=docs_ok,
                phone_ok=phone_ok,
                disponible=disp,
                suspendido=susp,
                restriccion_financiera=fin_rest,
            ):
                if docs_ok:
                    docs["ANTECEDENTES_PENALES"].estado = "APROBADO"
                    docs["ANTECEDENTES_PENALES"].fecha_emision_declarada = date.today() - timedelta(days=10)
                    docs["ANTECEDENTES_PENALES"].fecha_vencimiento = date.today() + timedelta(days=80)
                else:
                    docs["ANTECEDENTES_PENALES"].estado = "VENCIDO"
                    docs["ANTECEDENTES_PENALES"].fecha_emision_declarada = date.today() - timedelta(days=100)
                    docs["ANTECEDENTES_PENALES"].fecha_vencimiento = date.today() - timedelta(days=10)

                self.tech_user.telefono_verificado = phone_ok
                self.profile.disponible = disp
                self.profile.suspendido = susp
                self.profile.restriccion_financiera = fin_rest
                self.db.commit()

                expected_verified = docs_ok and phone_ok
                expected_enabled = docs_ok and phone_ok and disp and (not susp) and (not fin_rest)

                status_eval = get_my_activation_status({"uid": "tech-uid"}, self.db)
                self.assertEqual(status_eval["tecnico_verificado"], expected_verified)
                self.assertEqual(status_eval["habilitado_operativamente"], expected_enabled)
                self.assertEqual(status_eval["puede_recibir_contrataciones"], expected_enabled)

                catalog = get_technicians_catalog(nombre=None, especialidad=None, municipio=None, db=self.db)
                if expected_enabled:
                    self.assertEqual(len(catalog), 1)
                    accepted = accept_service_request(req_id, {"uid": "tech-uid"}, self.db)
                    self.assertEqual(accepted["status"], "accepted")
                else:
                    self.assertEqual(catalog, [])
                    with self.assertRaises(HTTPException) as exc:
                        accept_service_request(req_id, {"uid": "tech-uid"}, self.db)
                    self.assertEqual(exc.exception.status_code, 403)
                    self.assertGreaterEqual(len(status_eval["motivos_bloqueo"]), 1)


if __name__ == "__main__":
    unittest.main()
