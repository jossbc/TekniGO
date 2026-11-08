from datetime import date, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from fastapi import FastAPI, HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from app.admin_verification import AdminVerificationReviewSchema, DocumentReviewItem, apply_technician_review, get_technician_dossier, list_technician_dossiers, list_dossiers, router
from app.database import Base
from app.identity_documents import get_document_content_for_admin, router as documents_router
from app.identity_processing import process_current_identity
from app.models import DocumentReviewDecision, IdentityDocument, IdentityVerificationRequest, IdentityVerificationResult, TechnicianProfile, User, UserRole
from app.security import get_current_user
from app.technician_activation import REQUIRED_TECHNICIAN_DOCS
from app.technicians import upload_verification_documents


class AdminVerificationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.admin = self.add_user('admin', 'ADMIN', '1')
        self.tech = self.add_user('tech', 'TECNICO', '2')
        self.client = self.add_user('client', 'CLIENTE', '3')
        self.profile = TechnicianProfile(usuario_id=self.tech.id, disponible=True)
        self.db.add(self.profile)
        self.request, self.docs = self.add_version(1)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def add_user(self, uid, role, suffix):
        user = User(firebase_uid=uid, correo=uid+'@example.com', nombre_completo=uid,
                    dni='080120000000'+suffix, telefono='+5049000000'+suffix, telefono_verificado=True)
        self.db.add(user); self.db.flush()
        self.db.add(UserRole(usuario_id=user.id, rol=role)); self.db.flush()
        return user

    def add_version(self, version, omit=None):
        req = IdentityVerificationRequest(usuario_id=self.tech.id, version=version)
        self.db.add(req); self.db.flush()
        docs = {}
        for kind, _ in REQUIRED_TECHNICIAN_DOCS:
            if kind == omit: continue
            doc = IdentityDocument(usuario_id=self.tech.id, solicitud_id=req.id, tipo=kind,
                ruta_privada=f'{uuid.uuid4()}.enc', content_type='image/jpeg', tamano_bytes=10,
                sha256='a'*64, estado='PENDIENTE',
                fecha_emision_declarada=date.today() if kind.startswith('ANTECEDENTES') else None)
            self.db.add(doc); self.db.flush(); docs[kind] = doc
        return req, docs

    def review(self, docs=None, **kwargs):
        payload = AdminVerificationReviewSchema(documentos=[DocumentReviewItem(
            documento_id=doc.id, estado='APROBADO', legible=True, identidad_coincide=True,
        ) for doc in (docs or self.docs).values()], **kwargs)
        return apply_technician_review(self.tech.id, payload, {'uid':'admin'}, self.db)

    def test_ca01_routes_and_direct_file_access_deny_non_admins(self):
        # Exercise the same auth gates without requiring an OS network socket.
        app = FastAPI(); app.include_router(router); app.include_router(documents_router)
        paths = set(app.openapi()['paths'])
        self.assertIn('/admin/technicians', paths)
        self.assertIn('/admin/technicians/{usuario_id}/dossier', paths)
        self.assertIn('/admin/identity-documents/{document_id}/content', paths)
        with self.assertRaises(HTTPException) as caught: get_current_user(None)
        self.assertEqual(caught.exception.status_code, 401)
        for uid in ['tech', 'client']:
            claims = {'uid':uid}
            for action in [
                lambda: list_technician_dossiers(Response(), claims, self.db, 0, 20),
                lambda: get_technician_dossier(self.tech.id, Response(), claims, self.db),
                lambda: get_document_content_for_admin(self.docs['SELFIE'].id, claims, self.db),
                lambda: apply_technician_review(self.tech.id, AdminVerificationReviewSchema(documentos=[
                    DocumentReviewItem(documento_id=self.docs['SELFIE'].id, estado='APROBADO', legible=True, identidad_coincide=True)
                ]), claims, self.db),
            ]:
                with self.assertRaises(HTTPException) as caught: action()
                self.assertEqual(caught.exception.status_code, 403)
        response = Response()
        dossier = get_technician_dossier(self.tech.id, response, {'uid':'admin'}, self.db)
        self.assertIn('no-store', response.headers['cache-control'])
        self.assertEqual(len(dossier['documentos']), 5)

    def test_ca02_dossier_shows_identity_phone_and_required_files(self):
        dossier = get_technician_dossier(self.tech.id, Response(), {'uid':'admin'}, self.db)
        self.assertEqual(dossier['dni'], self.tech.dni)
        self.assertTrue(dossier['telefono_verificado'])
        self.assertEqual(dossier['version'], 1)
        self.assertNotIn('ruta_privada', str(dossier))

    def test_ca03_each_decision_preserves_actor_time_and_version(self):
        self.review()
        decisions = self.db.scalars(select(DocumentReviewDecision)).all()
        self.assertEqual(len(decisions), 5)
        self.assertTrue(all(d.administrador_id == self.admin.id and d.version_evaluada == 1 and d.creado_en for d in decisions))
        self.assertEqual({d.documento_id for d in decisions}, {d.id for d in self.docs.values()})

    def test_ca03_rejection_requires_nonblank_reason_and_approval_requires_legibility(self):
        for kwargs in [dict(estado='RECHAZADO', observaciones='  '), dict(estado='APROBADO')]:
            with self.assertRaises(ValidationError): DocumentReviewItem(documento_id=uuid.uuid4(), **kwargs)

    def test_ca04_missing_expired_and_identity_mismatch_never_complete_review(self):
        self.docs['ANTECEDENTES_PENALES'].fecha_vencimiento = date.today()-timedelta(days=1)
        self.db.commit()
        with self.assertRaises(HTTPException): self.review()
        self.assertEqual(self.db.scalars(select(DocumentReviewDecision)).all(), [])
        self.docs['ANTECEDENTES_PENALES'].fecha_vencimiento = None
        self.db.add(IdentityVerificationResult(solicitud_id=self.request.id, estado='RECHAZADO', motivo='No coincide', proceso_responsable='PROCESAMIENTO_IDENTIDAD', version_proceso='2'))
        self.db.commit()
        with self.assertRaises(HTTPException): self.review()
        self.db.rollback()
        _, latest = self.add_version(2, omit='SELFIE'); self.db.commit()
        result = self.review(latest)
        self.assertNotEqual(result['estado_verificacion'], 'VERIFICADO')
        self.assertFalse(result['puede_recibir_contrataciones'])

    def test_ca06_approvals_keep_suspension_and_financial_restrictions(self):
        self.profile.suspendido = True; self.profile.restriccion_financiera = True
        self.db.commit()
        result = self.review()
        self.assertEqual(result['estado_verificacion'], 'VERIFICADO')
        self.assertFalse(result['puede_recibir_contrataciones'])
        self.assertTrue(self.profile.suspendido)
        self.assertTrue(self.profile.restriccion_financiera)

    def test_ca07_stale_document_and_foreign_document_reject_entire_batch(self):
        old = self.docs
        _, latest = self.add_version(2); self.db.commit()
        with self.assertRaises(HTTPException) as caught: self.review(old)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertTrue(all(doc.estado == 'PENDIENTE' for doc in latest.values()))
        self.assertEqual(self.db.scalars(select(DocumentReviewDecision)).all(), [])
        with self.assertRaises(HTTPException): self.review({'fake': SimpleNamespace(id=uuid.uuid4())})

    def test_client_role_cannot_overwrite_an_existing_administrative_decision(self):
        self.db.add(UserRole(usuario_id=self.tech.id, rol='CLIENTE'))
        self.db.commit()
        self.db.expire_all()
        self.review()
        with self.assertRaises(HTTPException) as caught:
            process_current_identity({'uid':'tech'}, self.db)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertTrue(all(doc.estado == 'APROBADO' for doc in self.docs.values()))

    def test_ca05_resubmission_preserves_documents_decisions_and_rechecks_new_version(self):
        item = DocumentReviewItem(documento_id=self.docs['SELFIE'].id, estado='RECHAZADO', observaciones='Selfie borrosa')
        apply_technician_review(self.tech.id, AdminVerificationReviewSchema(documentos=[item]), {'uid':'admin'}, self.db)
        body = b'--bound\r\nContent-Disposition: form-data; name="selfie"; filename="selfie.jpg"\r\nContent-Type: image/jpeg\r\n\r\n\xff\xd8\xffnew-selfie\r\n--bound--\r\n'
        async def receive(): return {'type':'http.request', 'body':body, 'more_body':False}
        request = Request({'type':'http','method':'POST','path':'/', 'headers':[(b'content-type', b'multipart/form-data; boundary=bound')]}, receive)
        store = Mock()
        store.read_private.return_value = b'old-file'
        store.store.side_effect = lambda **kwargs: SimpleNamespace(ruta_privada=f'{uuid.uuid4()}.enc', content_type='image/jpeg', tamano_bytes=10, sha256='b'*64)
        with patch('app.technicians.PrivateDocumentStorage', return_value=store):
            task = upload_verification_documents(request, {'uid':'tech'}, self.db)
            try:
                task.send(None)
            except StopIteration as completed:
                result = completed.value
            else:
                self.fail('The in-memory upload unexpectedly required external I/O')
        self.assertEqual(len(self.db.scalars(select(IdentityDocument)).all()), 10)
        self.assertEqual(len(self.db.scalars(select(DocumentReviewDecision)).all()), 1)
        self.assertTrue(all(doc['estado']=='PENDIENTE' for doc in result['documentos']))
        self.assertEqual(self.docs['SELFIE'].estado, 'RECHAZADO')
        with self.assertRaises(HTTPException): self.review()

    def client_case(self, state="ERROR", version=1):
        req = IdentityVerificationRequest(usuario_id=self.client.id, version=version)
        self.db.add(req); self.db.flush()
        docs = []
        for kind in ("DNI_FRONTAL", "DNI_REVERSO", "SELFIE"):
            doc = IdentityDocument(usuario_id=self.client.id, solicitud_id=req.id, tipo=kind,
                ruta_privada=f"{uuid.uuid4()}.enc", content_type="image/jpeg", tamano_bytes=10,
                sha256="a"*64, estado="PENDIENTE")
            self.db.add(doc); docs.append(doc)
        if state:
            self.db.add(IdentityVerificationResult(solicitud_id=req.id, estado=state, motivo="Resultado de prueba",
                proceso_responsable="PROCESAMIENTO_IDENTIDAD", version_proceso="2"))
        self.client.estado_identidad = "VERIFICADO" if state == "VERIFICADO" else "EN_REVISION"
        self.db.commit()
        return req, docs

    def queue(self, tipo="TODOS", offset=0, limit=20):
        return list_dossiers(Response(), {"uid":"admin"}, self.db, offset, limit, tipo)

    def test_client_failures_enter_queue_automatically_with_roles_and_filters(self):
        self.assertEqual(self.queue("CLIENTE")["total"], 0)
        for state in ("ERROR", "INCIERTO", "RECHAZADO"):
            if self.db.scalar(select(IdentityVerificationRequest).where(IdentityVerificationRequest.usuario_id == self.client.id)):
                result = self.db.scalar(select(IdentityVerificationResult).join(IdentityVerificationRequest).where(IdentityVerificationRequest.usuario_id == self.client.id))
                result.estado = state; self.db.commit()
            else:
                self.client_case(state)
            self.assertEqual(self.queue()["total"], 2)
            clients = self.queue("CLIENTE")
            self.assertEqual(clients["total"], 1)
            self.assertEqual(clients["expedientes"][0]["roles"], ["CLIENTE"])
            self.assertEqual(self.queue("TECNICO")["total"], 1)
        self.db.add(UserRole(usuario_id=self.tech.id, rol="CLIENTE")); self.db.commit(); self.db.expire_all()
        self.assertEqual(self.queue()["total"], 2)
        self.assertEqual(len(self.queue(offset=1, limit=1)["expedientes"]), 1)

    def test_queue_uses_current_version_excludes_unprocessed_and_verified_clients(self):
        self.client_case("ERROR")
        self.assertEqual(self.queue("CLIENTE")["total"], 1)
        self.client_case(None, version=2)
        self.assertEqual(self.queue("CLIENTE")["total"], 0)
        self.client_case("VERIFICADO", version=3)
        self.assertEqual(self.queue("CLIENTE")["total"], 0)

    def test_admin_can_review_client_without_backgrounds_preserving_phone_and_history(self):
        req, docs = self.client_case("INCIERTO")
        self.client.telefono_verificado = False; self.db.commit()
        dossier = get_technician_dossier(self.client.id, Response(), {"uid":"admin"}, self.db)
        self.assertEqual(len(dossier["documentos"]), 3)
        self.assertIsNone(dossier["estado_verificacion"])
        for doc in docs:
            result = apply_technician_review(self.client.id, AdminVerificationReviewSchema(documentos=[
                DocumentReviewItem(documento_id=doc.id, estado="APROBADO", legible=True, identidad_coincide=True)
            ]), {"uid":"admin"}, self.db)
        self.assertEqual(result["estado_identidad"], "VERIFICADO")
        self.assertTrue(result["revision_completa"])
        self.assertFalse(self.client.telefono_verificado)
        self.assertEqual(self.queue("CLIENTE")["total"], 0)
        self.assertEqual(len(self.db.scalars(select(DocumentReviewDecision)).all()), 3)
        self.assertEqual(len(self.db.scalars(select(IdentityVerificationResult).where(IdentityVerificationResult.solicitud_id == req.id)).all()), 4)

    def test_client_queue_permissions_and_unresolved_discrepancy(self):
        _, docs = self.client_case("RECHAZADO")
        with self.assertRaises(HTTPException) as caught:
            list_dossiers(Response(), {"uid":"client"}, self.db, 0, 20, "CLIENTE")
        self.assertEqual(caught.exception.status_code, 403)
        with self.assertRaises(HTTPException) as caught:
            apply_technician_review(self.client.id, AdminVerificationReviewSchema(documentos=[
                DocumentReviewItem(documento_id=docs[0].id, estado="APROBADO", legible=True, identidad_coincide=True)
            ]), {"uid":"admin"}, self.db)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertNotEqual(self.client.estado_identidad, "VERIFICADO")

    def test_manual_resolution_of_machine_rejection_requires_reason_and_preserves_original(self):
        req, docs = self.client_case("RECHAZADO")
        for doc in docs:
            result = apply_technician_review(self.client.id, AdminVerificationReviewSchema(documentos=[
                DocumentReviewItem(documento_id=doc.id, estado="APROBADO", legible=True,
                    identidad_coincide=True, observaciones="Revisé DNI y selfie: el reflejo causó el rechazo automático; los datos y el rostro coinciden.")
            ]), {"uid":"admin"}, self.db)
        self.assertEqual(result["estado_identidad"], "VERIFICADO")
        original = self.db.scalar(select(IdentityVerificationResult).where(
            IdentityVerificationResult.solicitud_id == req.id,
            IdentityVerificationResult.proceso_responsable == "PROCESAMIENTO_IDENTIDAD"))
        self.assertEqual(original.estado, "RECHAZADO")
        decisions = self.db.scalars(select(DocumentReviewDecision).where(DocumentReviewDecision.solicitud_id == req.id)).all()
        self.assertEqual(len(decisions), 3)
        self.assertTrue(all(d.observaciones and d.administrador_id == self.admin.id for d in decisions))
        manual = self.db.scalars(select(IdentityVerificationResult).where(
            IdentityVerificationResult.solicitud_id == req.id,
            IdentityVerificationResult.proceso_responsable == "REVISION_ADMINISTRATIVA_IDENTIDAD")).all()
        self.assertTrue(all(r.evidencia["resultado_automatico_id"] == str(original.id) and r.evidencia["revision_manual_de_rechazo"] for r in manual))

    def test_final_technician_approval_verifies_and_removes_from_pending_queue(self):
        self.assertEqual(self.queue("TECNICO")["total"], 1)
        for kind, doc in self.docs.items():
            result = self.review({kind: doc})
        self.assertTrue(result["revision_completa"])
        self.assertEqual(result["estado_identidad"], "VERIFICADO")
        self.assertEqual(result["estado_verificacion"], "VERIFICADO")
        self.assertEqual(self.queue()["total"], 0)
        self.assertEqual(self.queue("TECNICO")["total"], 0)
        self.assertEqual(len(get_technician_dossier(self.tech.id, Response(), {"uid":"admin"}, self.db)["decisiones"]), 5)

    def test_three_identity_approvals_verify_identity_but_not_technician_backgrounds(self):
        basic = {kind: doc for kind, doc in self.docs.items() if not kind.startswith("ANTECEDENTES")}
        result = self.review(basic)
        self.assertEqual(result["estado_identidad"], "VERIFICADO")
        self.assertFalse(result["revision_completa"])
        self.assertNotEqual(result["estado_verificacion"], "VERIFICADO")
        self.assertEqual(self.queue("TECNICO")["total"], 1)


if __name__ == '__main__': unittest.main()
