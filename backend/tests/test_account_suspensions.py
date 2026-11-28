"""HU-52 ST-06: Pruebas completas de suspensiones, reinscripción, restricciones simultáneas, tercera falta, vencimiento y cierre."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import unittest
from unittest.mock import patch
import uuid

from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.account_closure_service import (
    AccountClosureService,
    DocumentSoftDeleteHandler,
    IdentityDocumentSoftDeleteHandler,
    TradeCertificateSoftDeleteHandler,
)
from app.account_suspension_domain import (
    DimensionEvaluationResult,
    OperationalDimension,
    OperationalDimensionEvaluator,
    SuspensionConflictError,
    SuspensionForbiddenError,
    SuspensionValidationError,
)
from app.account_suspension_service import (
    AccountOperationalPolicy,
    AccountSuspensionService,
    DEFAULT_DIMENSION_EVALUATORS,
    assert_client_can_operate,
    assert_identity_not_blocked,
    assert_technician_can_operate,
    run_scheduled_suspension_expiration,
)
from app.cancellation_justification_service import (
    JustificationDecision,
    resolve_justification,
    submit_justification,
)
from app.cancellation_policy import CancellationCause, default_cancellation_policy
from app.cancellation_repository import (
    SqlAlchemyCancellationRepository,
    SqlAlchemyJustificationRepository,
)
from app.cancellation_service import cancel_request
from app.certifications import approved_certificate_owners, download_certificate
from app.database import Base
from app.direct_requests_schemas import CreateDirectRequest
from app.direct_requests_service import DirectRequestService
from app.fault_recidivism import FaultComputationService, RecidivismOnCancellationObserver
from app.identity_documents import get_document_content_for_admin
from app.models import (
    ACCOUNT_DELETED,
    AccountSuspensionAudit,
    DirectRequest,
    FinancialLedgerEntry,
    IdentityDocument,
    IdentityVerificationRequest,
    RecidivismAlertEvent,
    Service,
    Specialty,
    TechnicianProfile,
    TradeCertificate,
    User,
    UserRole,
)
from app.technician_direct_requests import respond_to_direct_request
from app.technicians import get_technician_public_profile, get_technicians_catalog
from app.users import ProfileUpdate, Registration, RoleAddition, add_role, delete_my_account, register, update_me


class _DummyEvidenceStore:
    def ensure_owned(self, user_id, refs):
        return None


class AccountSuspensionsHu52Tests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _seed_actors(self, suffix: str):
        idx = abs(hash(suffix)) % 80000
        admin = User(
            id=uuid.uuid4(),
            firebase_uid=f"adm-{suffix}",
            nombre_completo=f"Admin {suffix}",
            correo=f"adm-{suffix}@teknigo.test",
            dni=f"08011980{idx:05d}",
            telefono=f"+5049{idx:07d}",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        cli = User(
            id=uuid.uuid4(),
            firebase_uid=f"cli-{suffix}",
            nombre_completo=f"Cliente {suffix}",
            correo=f"cli-{suffix}@teknigo.test",
            dni=f"08011990{idx:05d}",
            telefono=f"+5048{idx:07d}",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        tec = User(
            id=uuid.uuid4(),
            firebase_uid=f"tec-{suffix}",
            nombre_completo=f"Tecnico {suffix}",
            correo=f"tec-{suffix}@teknigo.test",
            dni=f"08011995{idx:05d}",
            telefono=f"+5043{idx:07d}",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        spec = Specialty(id=uuid.uuid4(), nombre=f"Oficio {suffix}", activa=True)
        self.db.add_all([admin, cli, tec, spec])
        self.db.flush()
        profile = TechnicianProfile(
            usuario_id=tec.id,
            disponible=True,
            estado_verificacion="VERIFICADO",
            restriccion_financiera=False,
        )
        self.db.add_all([
            UserRole(usuario_id=admin.id, rol="ADMIN"),
            UserRole(usuario_id=cli.id, rol="CLIENTE"),
            UserRole(usuario_id=tec.id, rol="TECNICO"),
            profile,
        ])
        self.db.flush()
        srv = Service(
            id=uuid.uuid4(),
            usuario_id=tec.id,
            especialidad_id=spec.id,
            nombre=f"Servicio {suffix}",
            descripcion="Servicio de prueba",
            tarifa_base=Decimal("400.00"),
            unidad_cobro="SERVICIO",
            activo=True,
        )
        self.db.add(srv)
        ver_req = IdentityVerificationRequest(usuario_id=tec.id, version=1)
        self.db.add(ver_req)
        self.db.flush()
        today = date.today()
        for doc_type in ("DNI_FRONTAL", "DNI_REVERSO", "SELFIE", "ANTECEDENTES_POLICIALES", "ANTECEDENTES_PENALES"):
            self.db.add(
                IdentityDocument(
                    usuario_id=tec.id,
                    solicitud_id=ver_req.id,
                    tipo=doc_type,
                    ruta_privada=f"priv/{tec.id}/{doc_type.lower()}.jpg",
                    content_type="image/jpeg",
                    tamano_bytes=1024,
                    sha256="c" * 64,
                    estado="APROBADO",
                    fecha_emision_declarada=today,
                    fecha_vencimiento=(today + timedelta(days=60)) if "ANTECEDENTES" in doc_type else None,
                )
            )
        self.db.commit()
        return admin, cli, tec, profile, srv

    def test_ca01_ca02_only_admin_suspends_blocks_operations_and_hides_from_catalog(self):
        admin, cli, tec, profile, srv = self._seed_actors("ca12")
        svc = AccountSuspensionService(self.db)
        now = datetime.now(timezone.utc)

        # CA-01: cuentas no administradoras no pueden suspender
        with self.assertRaises(SuspensionForbiddenError):
            svc.apply_suspension(cli, tec.id, tipo="TEMPORAL", causa="CONDUCTA", motivo="Intento no autorizado", vence_en=now + timedelta(days=2), now=now)

        # CA-01: validaciones de fecha de vencimiento
        with self.assertRaises(SuspensionValidationError):
            svc.apply_suspension(admin, tec.id, tipo="TEMPORAL", causa="CONDUCTA", motivo="Sin fecha", vence_en=None, now=now)
        with self.assertRaises(SuspensionValidationError):
            svc.apply_suspension(admin, tec.id, tipo="DEFINITIVA", causa="CONDUCTA", motivo="Con fecha", vence_en=now + timedelta(days=1), now=now)

        # Trabajo ya aceptado NO debe cancelarse automáticamente al suspender (Condición transversal)
        accepted_req = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=cli.firebase_uid,
            tecnico_id=tec.firebase_uid,
            servicio_id=srv.id,
            municipio="Tegucigalpa",
            sector="Centro",
            direccion_exacta="Col. Centro",
            fecha_hora=now + timedelta(days=2),
            descripcion="Trabajo ya aceptado",
            cantidad=Decimal("1.00"),
            tarifa_aplicada=Decimal("400.00"),
            unidad_cobro="SERVICIO",
            metodo_pago="efectivo",
            monto_base=Decimal("400.00"),
            monto_total=Decimal("400.00"),
            estado="ACEPTADA",
        )
        pending_req = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=cli.firebase_uid,
            tecnico_id=tec.firebase_uid,
            servicio_id=srv.id,
            municipio="Tegucigalpa",
            sector="Centro",
            direccion_exacta="Col. Centro",
            fecha_hora=now + timedelta(days=3),
            descripcion="Solicitud pendiente",
            cantidad=Decimal("1.00"),
            tarifa_aplicada=Decimal("400.00"),
            unidad_cobro="SERVICIO",
            metodo_pago="efectivo",
            monto_base=Decimal("400.00"),
            monto_total=Decimal("400.00"),
            estado="PENDIENTE",
        )
        self.db.add_all([accepted_req, pending_req])
        self.db.commit()

        # Aplicar suspensión temporal al técnico
        res = svc.apply_suspension(
            admin,
            tec.id,
            tipo="TEMPORAL",
            causa="CONDUCTA",
            motivo="Incumplimiento verificado de normas de servicio",
            vence_en=now + timedelta(days=5),
            now=now,
        )
        self.assertEqual(res["suspension"]["estado"], "ACTIVA")
        self.assertEqual(res["suspension"]["responsable_admin_id"], str(admin.id))
        self.assertIsNotNone(res["suspension"]["vence_en"])

        # CA-02: sale del catálogo y del perfil público; no puede aceptar trabajos pendientes; trabajo aceptado permanece ACEPTADA
        catalog = get_technicians_catalog(nombre=None, especialidad=None, municipio=None, db=self.db)
        self.assertTrue(all(item["usuario_id"] != str(tec.id) for item in catalog))
        with self.assertRaises(HTTPException) as prof_err:
            get_technician_public_profile(tec.id, self.db)
        self.assertEqual(prof_err.exception.status_code, 404)

        with self.assertRaises(HTTPException) as accept_err:
            respond_to_direct_request(pending_req.id, "ACEPTADA", {"uid": tec.firebase_uid}, self.db)
        self.assertEqual(accept_err.exception.status_code, 403)

        self.db.refresh(accepted_req)
        self.assertEqual(accepted_req.estado, "ACEPTADA")

        # CA-02: cliente suspendido no puede crear contrataciones directas ni agregar roles
        svc.apply_suspension(admin, cli.id, tipo="DEFINITIVA", causa="INCUMPLIMIENTO_REGLAS", motivo="Incumplimiento grave del cliente", now=now)
        with self.assertRaises(HTTPException) as hire_err:
            DirectRequestService.create_direct_request(
                self.db,
                CreateDirectRequest(
                    tecnico_id=str(tec.id),
                    servicio_id=str(srv.id),
                    municipio="Tegucigalpa",
                    sector="Centro",
                    direccion_exacta="Col. Prueba",
                    fecha_hora=now + timedelta(days=2),
                    descripcion="Intento de contratación estando suspendido",
                    cantidad=Decimal("1.00"),
                    metodo_pago="efectivo",
                ),
                {"uid": cli.firebase_uid},
            )
        self.assertEqual(hire_err.exception.status_code, 403)

        with self.assertRaises(HTTPException) as role_err:
            add_role(RoleAddition(rol="TECNICO"), {"uid": cli.firebase_uid}, self.db)
        self.assertEqual(role_err.exception.status_code, 403)

    def test_ca03_ca04_third_fault_unique_alert_no_auto_suspension_and_justification_recalculation(self):
        admin, cli, tec, _, srv = self._seed_actors("ca34")
        now = datetime.now(timezone.utc)
        req_ids: list[str] = []

        # 3 cancelaciones tardías -> alerta única sin suspensión automática (CA-03)
        for i in range(4):
            r = DirectRequest(
                id=str(uuid.uuid4()),
                cliente_id=cli.firebase_uid,
                tecnico_id=tec.firebase_uid,
                servicio_id=srv.id,
                municipio="Tegucigalpa",
                sector="Centro",
                direccion_exacta="Dir",
                fecha_hora=now + timedelta(hours=4),
                descripcion=f"Trabajo {i}",
                cantidad=Decimal("1.00"),
                tarifa_aplicada=Decimal("400.00"),
                unidad_cobro="SERVICIO",
                metodo_pago="efectivo",
                monto_base=Decimal("400.00"),
                monto_total=Decimal("400.00"),
                estado="ACEPTADA",
            )
            self.db.add(r)
            self.db.flush()
            req_ids.append(r.id)
            if i < 3:
                cancel_request(
                    self.db,
                    r.id,
                    causa=CancellationCause.VOLUNTARIA,
                    motivo=f"Cancelación tardía {i}",
                    evidencias=[],
                    claims={"uid": tec.firebase_uid},
                    policy=default_cancellation_policy(),
                    repository=SqlAlchemyCancellationRepository(),
                    observers=[RecidivismOnCancellationObserver()],
                    now=now + timedelta(minutes=i),
                )

        self.db.refresh(tec)
        self.assertFalse(tec.suspendido)
        alerts = list(self.db.scalars(select(RecidivismAlertEvent).where(RecidivismAlertEvent.usuario_uid == tec.firebase_uid)).all())
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].estado, "PENDIENTE_DECISION_HU52")
        self.assertFalse(alerts[0].suspension_automatica_aplicada)

        # Una 4.ª falta con la alerta aún pendiente NO duplica el evento de alerta
        cancel_request(
            self.db,
            req_ids[3],
            causa=CancellationCause.VOLUNTARIA,
            motivo="Cuarta cancelación tardía",
            evidencias=[],
            claims={"uid": tec.firebase_uid},
            policy=default_cancellation_policy(),
            repository=SqlAlchemyCancellationRepository(),
            observers=[RecidivismOnCancellationObserver()],
            now=now + timedelta(minutes=10),
        )
        alerts_after_4th = list(self.db.scalars(select(RecidivismAlertEvent).where(RecidivismAlertEvent.usuario_uid == tec.firebase_uid)).all())
        self.assertEqual(len(alerts_after_4th), 1)
        self.assertEqual(alerts_after_4th[0].total_faltas_computadas, 4)

        # CA-04: aprobar dos justificaciones reduce el cómputo de 4 a 2, invalida la alerta pendiente y conserva falta original + decisión
        for idx in (0, 1):
            just = submit_justification(
                self.db,
                req_ids[idx],
                motivo=f"Justificación médica {idx}",
                evidencias=[],
                claims={"uid": tec.firebase_uid},
                cancellations=SqlAlchemyCancellationRepository(),
                justifications=SqlAlchemyJustificationRepository(),
                evidence=_DummyEvidenceStore(),
            )
            resolve_justification(
                self.db,
                just["id"],
                decision=JustificationDecision.APROBAR,
                observaciones=f"Evidencia verificada {idx}",
                claims={"uid": admin.firebase_uid},
                cancellations=SqlAlchemyCancellationRepository(),
                justifications=SqlAlchemyJustificationRepository(),
            )

        self.db.refresh(alerts_after_4th[0])
        self.assertEqual(alerts_after_4th[0].estado, "INVALIDADO_POR_JUSTIFICACION")
        summary = FaultComputationService().compute_summary(self.db, tec.firebase_uid)
        self.assertEqual(summary["total_faltas_no_justificadas"], 2)
        self.assertEqual(summary["total_faltas_excluidas_justificadas"], 2)
        self.assertEqual(summary["faltas_excluidas"][0]["estado_incumplimiento"], "JUSTIFICADA")
        self.assertEqual(summary["faltas_excluidas"][0]["decision_justificacion"]["estado"], "APROBADA")

    def test_ca05_ca07_simultaneous_restrictions_lift_and_expiration(self):
        admin, _, tec, profile, _ = self._seed_actors("ca57")
        svc = AccountSuspensionService(self.db)
        now = datetime.now(timezone.utc)

        # CA-07: la restricción financiera no puede registrarse como causa de suspensión por conducta
        with self.assertRaises(SuspensionValidationError):
            svc.apply_suspension(admin, tec.id, tipo="TEMPORAL", causa="RESTRICCION_FINANCIERA", motivo="Deuda acumulada", vence_en=now + timedelta(days=2), now=now)

        # Activar restricciones simultáneas: restricción financiera + No disponible + suspensión temporal
        profile.restriccion_financiera = True
        profile.disponible = False
        self.db.commit()

        applied = svc.apply_suspension(admin, tec.id, tipo="TEMPORAL", causa="CONDUCTA", motivo="Suspensión temporal de prueba", vence_en=now + timedelta(days=3), now=now)
        self.assertTrue(applied["estado_operativo"]["suspendido_por_conducta"])
        self.assertTrue(applied["estado_operativo"]["restriccion_financiera"])

        # CA-05: levantar la suspensión temporal registra auditoría pero NO restituye operaciones mientras persistan saldo/disponibilidad
        lifted = svc.lift_temporal_suspension(admin, uuid.UUID(applied["suspension"]["id"]), motivo_levantamiento="Revisión favorable", now=now + timedelta(hours=12))
        self.assertEqual(lifted["suspension"]["estado"], "LEVANTADA")
        self.assertFalse(lifted["operaciones_restituidas"])
        self.assertFalse(lifted["estado_operativo"]["suspendido_por_conducta"])
        self.assertTrue(lifted["estado_operativo"]["restriccion_financiera"])
        with self.assertRaises(HTTPException) as fin_err:
            assert_technician_can_operate(self.db, tec, profile)
        self.assertIn("restricción financiera", fin_err.exception.detail.lower())

        # Segunda suspensión temporal que vence por reloj cuando verificación, disponibilidad y saldo SÍ están en regla
        profile.restriccion_financiera = False
        profile.disponible = True
        self.db.commit()
        applied2 = svc.apply_suspension(admin, tec.id, tipo="TEMPORAL", causa="INCUMPLIMIENTO_REGLAS", motivo="Segunda suspensión temporal", vence_en=now + timedelta(days=4), now=now + timedelta(days=1))
        exp_res = run_scheduled_suspension_expiration(self.db, now=now + timedelta(days=5))
        self.assertEqual(exp_res["total_expiradas"], 1)
        self.assertTrue(exp_res["detalle"][0]["operaciones_restituidas"])
        self.db.refresh(tec)
        self.db.refresh(profile)
        self.assertFalse(tec.suspendido)
        self.assertFalse(profile.suspendido)

        # Reaparece habilitado en el catálogo al vencerse y cumplirse las demás dimensiones
        catalog = get_technicians_catalog(nombre=None, especialidad=None, municipio=None, db=self.db)
        self.assertTrue(any(item["usuario_id"] == str(tec.id) for item in catalog))

        # Suspensión definitiva no puede levantarse mediante flujo temporal
        definitive = svc.apply_suspension(admin, tec.id, tipo="DEFINITIVA", causa="FRAUDE_IDENTIDAD", motivo="Infracción definitiva", now=now + timedelta(days=6))
        with self.assertRaises(SuspensionConflictError):
            svc.lift_temporal_suspension(admin, uuid.UUID(definitive["suspension"]["id"]), motivo_levantamiento="Intento", now=now + timedelta(days=7))

    def test_ca06_suspension_blocks_reregistration_and_preserves_history_and_debts(self):
        admin, cli, tec, profile, srv = self._seed_actors("ca06")
        svc = AccountSuspensionService(self.db)
        now = datetime.now(timezone.utc)

        # Sembrar historial de solicitud y deuda/libro financiero previo
        profile.servicios_efectivo_sin_liquidar = 2
        req = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=cli.firebase_uid,
            tecnico_id=tec.firebase_uid,
            servicio_id=srv.id,
            municipio="Tegucigalpa",
            sector="Centro",
            direccion_exacta="Dir",
            fecha_hora=now - timedelta(days=2),
            descripcion="Servicio previo",
            cantidad=Decimal("1.00"),
            tarifa_aplicada=Decimal("400.00"),
            unidad_cobro="SERVICIO",
            metodo_pago="efectivo",
            monto_base=Decimal("400.00"),
            monto_total=Decimal("400.00"),
            estado="COMPLETADA",
        )
        ledger = FinancialLedgerEntry(
            clave_idempotencia=f"LEDGER-CA06-{req.id}",
            modalidad="DIRECTA",
            solicitud_id=req.id,
            tipo_movimiento="COMISION_EFECTIVO_PENDIENTE",
            monto_base=Decimal("400.00"),
            monto_ajuste=Decimal("0.00"),
            monto_total=Decimal("400.00"),
            tasa_comision=Decimal("0.1000"),
            monto_comision=Decimal("40.00"),
            metodo_pago="efectivo",
            detalle={},
            creado_en=now - timedelta(days=2),
        )
        self.db.add_all([req, ledger])
        self.db.commit()

        orig_dni, orig_phone = tec.dni, tec.telefono
        svc.apply_suspension(admin, tec.id, tipo="DEFINITIVA", causa="CONDUCTA", motivo="Suspensión definitiva CA-06", now=now)

        # CA-06: conserva DNI, teléfono, historial, solicitudes y deuda
        self.db.refresh(tec)
        self.db.refresh(profile)
        self.assertEqual(tec.dni, orig_dni)
        self.assertEqual(tec.telefono, orig_phone)
        self.assertEqual(profile.servicios_efectivo_sin_liquidar, 2)
        self.assertIsNotNone(self.db.get(DirectRequest, req.id))
        self.assertIsNotNone(self.db.scalar(select(FinancialLedgerEntry).where(FinancialLedgerEntry.solicitud_id == req.id)))

        # CA-06: no permite reinscribir otra cuenta con el mismo DNI ni con el mismo teléfono
        with self.assertRaises(HTTPException) as reg_dni_err:
            register(
                Registration(nombre_completo="Cuenta Clon DNI", dni=orig_dni, telefono="+50499991111", rol="TECNICO"),
                {"uid": "nuevo-uid-dni", "email": "clon1@teknigo.test"},
                self.db,
            )
        self.assertEqual(reg_dni_err.exception.status_code, 409)

        with self.assertRaises(HTTPException) as reg_phone_err:
            register(
                Registration(nombre_completo="Cuenta Clon Tel", dni="0801200199999", telefono=orig_phone, rol="CLIENTE"),
                {"uid": "nuevo-uid-tel", "email": "clon2@teknigo.test"},
                self.db,
            )
        self.assertEqual(reg_phone_err.exception.status_code, 409)

        # Tampoco permite que otro usuario cambie su teléfono al teléfono bloqueado
        with self.assertRaises(HTTPException) as upd_err:
            update_me(ProfileUpdate(telefono=orig_phone), {"uid": cli.firebase_uid}, self.db)
        self.assertEqual(upd_err.exception.status_code, 409)

    @patch("app.users.auth.delete_user")
    def test_ca08_account_closure_soft_deletes_docs_revokes_access_and_blocks_identity_reuse(self, mock_delete_user):
        admin, _, tec, _, _ = self._seed_actors("ca08")
        orig_dni, orig_phone = tec.dni, tec.telefono

        ver_req = IdentityVerificationRequest(usuario_id=tec.id, version=2)
        self.db.add(ver_req)
        self.db.flush()
        doc = IdentityDocument(
            usuario_id=tec.id,
            solicitud_id=ver_req.id,
            tipo="DNI_FRONTAL",
            ruta_privada=f"priv/{tec.id}/dni.jpg",
            content_type="image/jpeg",
            tamano_bytes=2048,
            sha256="b" * 64,
            estado="APROBADO",
        )
        cert = TradeCertificate(
            usuario_id=tec.id,
            titulo="Certificación eléctrica",
            ruta_privada=f"priv/{tec.id}/cert.pdf",
            content_type="application/pdf",
            estado="APROBADO",
        )
        self.db.add_all([doc, cert])
        self.db.commit()

        # Cierre tramitado por el titular vía DELETE /users/me (también cubre política CA-08)
        delete_my_account({"uid": tec.firebase_uid, "auth_time": datetime.now(timezone.utc).timestamp()}, self.db)
        mock_delete_user.assert_called_once_with(tec.firebase_uid)

        self.db.refresh(tec)
        self.db.refresh(doc)
        self.db.refresh(cert)
        self.assertEqual(tec.estado_identidad, ACCOUNT_DELETED)
        self.assertTrue(doc.baja_logica)
        self.assertTrue(doc.acceso_operativo_revocado)
        self.assertEqual(doc.estado, "INVALIDADO")
        self.assertTrue(cert.baja_logica)
        self.assertTrue(cert.acceso_operativo_revocado)
        self.assertEqual(cert.estado, "REVOCADO")
        self.assertNotIn(tec.id, approved_certificate_owners(self.db, [tec.id]))

        # Acceso operativo a descarga de documentos queda revocado (410) y no se borra el registro de conservación
        with self.assertRaises(HTTPException) as doc_err:
            get_document_content_for_admin(doc.id, {"uid": admin.firebase_uid}, self.db)
        self.assertEqual(doc_err.exception.status_code, 410)
        with self.assertRaises(HTTPException) as cert_err:
            download_certificate(cert.id, {"uid": admin.firebase_uid}, self.db)
        self.assertEqual(cert_err.exception.status_code, 410)

        # No permite reutilizar DNI ni teléfono tras el cierre aunque usuarios esté anonimizado
        with self.assertRaises(HTTPException) as reuse_dni:
            register(
                Registration(nombre_completo="Reingreso", dni=orig_dni, telefono="+50499112233", rol="CLIENTE"),
                {"uid": "uid-reingreso-1", "email": "reingreso1@teknigo.test"},
                self.db,
            )
        self.assertEqual(reuse_dni.exception.status_code, 409)
        with self.assertRaises(HTTPException) as reuse_phone:
            register(
                Registration(nombre_completo="Reingreso Tel", dni="0801200288888", telefono=orig_phone, rol="CLIENTE"),
                {"uid": "uid-reingreso-2", "email": "reingreso2@teknigo.test"},
                self.db,
            )
        self.assertEqual(reuse_phone.exception.status_code, 409)

        audits = list(self.db.scalars(select(AccountSuspensionAudit).where(AccountSuspensionAudit.usuario_id == tec.id)).all())
        events = {a.evento for a in audits}
        self.assertIn("CIERRE_CUENTA_APLICADO", events)
        self.assertIn("BAJA_LOGICA_DOCUMENTAL", events)

    def test_ocp_extensible_evaluators_and_soft_delete_handlers(self):
        _, _, tec, _, _ = self._seed_actors("ocp")

        class CustomDimensionEvaluator(OperationalDimensionEvaluator):
            @property
            def dimension(self) -> OperationalDimension:
                return OperationalDimension.DISPONIBILIDAD

            def evaluate(self, *, user, profile, active_suspension) -> DimensionEvaluationResult:
                return DimensionEvaluationResult(dimension=self.dimension, habilitada=False, motivo="Regla extendida OCP")

        policy = AccountOperationalPolicy(evaluators=(*DEFAULT_DIMENSION_EVALUATORS[:-1], CustomDimensionEvaluator()))
        eval_res = policy.evaluate_dimensions(self.db, tec)
        self.assertFalse(eval_res["habilitado_operativamente"])
        self.assertIn("Regla extendida OCP", eval_res["motivos_bloqueo"])

        called = []

        class CustomDocHandler(DocumentSoftDeleteHandler):
            @property
            def category(self) -> str:
                return "EXTRA_DOCS"

            def soft_delete_for_user(self, db, user_id, *, motivo, moment) -> int:
                called.append(user_id)
                return 3

        closure = AccountClosureService(
            self.db,
            handlers=(IdentityDocumentSoftDeleteHandler(), TradeCertificateSoftDeleteHandler(), CustomDocHandler()),
        )
        res = closure.apply_closure_to_user(tec, motivo="Prueba OCP cierre", proceso_responsable="TEST_OCP")
        self.assertEqual(called, [tec.id])
        self.assertEqual(res["documentos_baja_logica"]["EXTRA_DOCS"], 3)


if __name__ == "__main__":
    unittest.main()