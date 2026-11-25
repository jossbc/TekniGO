import unittest
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.main import app as fastapi_app
from app.models import (
    DirectRequest,
    OpenRequest,
    RequestStateEvent,
    Service,
    ServiceReview,
    Specialty,
    TechnicianProfile,
    User,
    UserRole,
)
from app.reviews import (
    BilateralMutualPublicationStrategy,
    CreateReviewSchema,
    DirectReviewableRequestAdapter,
    OpenReviewableRequestAdapter,
    SilentTimeoutPublicationStrategy,
    create_service_review,
    evaluate_review_publication_policy,
    get_service_reviews_status_data,
    get_user_reputation_summary,
    sync_expired_reviews,
)
from app.service_completion import report_service_completion
from app.service_reception import (
    ConfirmReceptionSchema,
    confirm_service_reception_by_client,
)


class ReviewsAndReputationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        # Crear usuarios: Cliente, Técnico, y Tercero
        self.client_user = User(
            id=uuid.uuid4(),
            firebase_uid="uid_cliente_1",
            nombre_completo="María Rodríguez",
            correo="maria@test.com",
            dni="0801199012345",
            telefono="+50499001122",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.tech_user = User(
            id=uuid.uuid4(),
            firebase_uid="uid_tecnico_1",
            nombre_completo="Carlos Méndez",
            correo="carlos@test.com",
            dni="0801198554321",
            telefono="+50498197602",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.other_user = User(
            id=uuid.uuid4(),
            firebase_uid="uid_ajeno_1",
            nombre_completo="Pedro Desconocido",
            correo="pedro@test.com",
            dni="0801200099887",
            telefono="+50497003344",
            telefono_verificado=True,
            estado_identidad="VERIFICADO",
        )
        self.db.add_all([self.client_user, self.tech_user, self.other_user])
        self.db.flush()

        self.db.add(UserRole(usuario_id=self.client_user.id, rol="CLIENTE"))
        self.db.add(UserRole(usuario_id=self.tech_user.id, rol="TECNICO"))
        self.db.add(
            TechnicianProfile(
                usuario_id=self.tech_user.id,
                disponible=True,
                estado_verificacion="VERIFICADO",
                calificacion_promedio=None,
            )
        )

        self.spec = Specialty(id=uuid.uuid4(), nombre="Electricidad")
        self.service = Service(
            id=uuid.uuid4(),
            usuario_id=self.tech_user.id,
            especialidad_id=self.spec.id,
            nombre="Instalación de tomacorrientes",
            descripcion="Cambio de tomas residenciales",
            tarifa_base=350.0,
            unidad_cobro="POR_HORA",
            activo=True,
        )
        self.db.add_all([self.spec, self.service])
        self.db.commit()

        # Solicitud directa completada vía seguimiento_recepcion (HU-35) + RequestStateEvent (HU-32)
        self.completion_time = datetime(2026, 11, 10, 10, 0, 0, tzinfo=timezone.utc)
        self.direct_req = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.client_user.firebase_uid,
            tecnico_id=self.tech_user.firebase_uid,
            servicio_id=self.service.id,
            municipio="Tegucigalpa",
            sector="Palmira",
            direccion_exacta="Calle 2",
            fecha_hora=self.completion_time,
            descripcion="Revisar tomacorrientes con chispa",
            cantidad=1.0,
            tarifa_aplicada=350.0,
            unidad_cobro="POR_HORA",
            monto_base=350.0,
            metodo_pago="efectivo",
            estado="COMPLETADA",
            creada_en=self.completion_time - timedelta(hours=2),
            seguimiento_recepcion={
                "completada": True,
                "fecha_cierre": self.completion_time.isoformat(),
                "resenas_habilitadas": True,
            },
        )
        self.db.add(self.direct_req)
        self.db.add(
            RequestStateEvent(
                modalidad="DIRECTA",
                solicitud_id=str(self.direct_req.id),
                estado="COMPLETADA",
                fecha=self.completion_time,
                responsable="CLIENTE",
                responsable_id=self.client_user.firebase_uid,
            )
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_routes_registered_on_fastapi_app(self):
        paths = fastapi_app.openapi()["paths"]
        self.assertIn("/reviews", paths)
        self.assertIn("/reviews/service/{tipo_solicitud}/{solicitud_id}", paths)
        self.assertIn("/reviews/users/{user_id}/reputation", paths)
        self.assertIn("/reviews/users/{user_id}", paths)
        self.assertIn("/reviews/sync-expirations", paths)

    def test_hu44_ca01_ca05_only_participant_of_completed_service_can_review(self):
        # 1. Servicio pendiente o en proceso no se puede calificar (400)
        self.direct_req.estado = "EN_PROCESO"
        self.db.commit()
        payload = CreateReviewSchema(
            tipo_solicitud="DIRECTA",
            solicitud_id=str(self.direct_req.id),
            puntuacion=5,
            comentario="Buen trabajo",
        )
        with self.assertRaises(HTTPException) as ctx:
            create_service_review(payload, {"uid": self.client_user.firebase_uid}, self.db)
        self.assertEqual(ctx.exception.status_code, 400)

        # 2. Restaurar a COMPLETADA y usuario ajeno no puede calificar (403)
        self.direct_req.estado = "COMPLETADA"
        self.db.commit()
        with self.assertRaises(HTTPException) as ctx_other:
            create_service_review(payload, {"uid": self.other_user.firebase_uid}, self.db)
        self.assertEqual(ctx_other.exception.status_code, 403)

    def test_hu44_hu45_ca02_validation_score_and_comment(self):
        with self.assertRaises(ValidationError):
            CreateReviewSchema(
                tipo_solicitud="DIRECTA",
                solicitud_id=str(self.direct_req.id),
                puntuacion=0,
                comentario="Válido",
            )
        with self.assertRaises(ValidationError):
            CreateReviewSchema(
                tipo_solicitud="DIRECTA",
                solicitud_id=str(self.direct_req.id),
                puntuacion=6,
                comentario="Válido",
            )
        with self.assertRaises(ValidationError):
            CreateReviewSchema(
                tipo_solicitud="DIRECTA",
                solicitud_id=str(self.direct_req.id),
                puntuacion=5,
                comentario="   ",
            )

    def test_hu44_hu45_ca03_uniqueness_prevents_duplicate_reviews(self):
        payload = CreateReviewSchema(
            tipo_solicitud="DIRECTA",
            solicitud_id=str(self.direct_req.id),
            puntuacion=5,
            comentario="Excelente servicio y puntualidad",
            aspectos_destacados=["Muy rápido", "Puntual"],
        )
        res = create_service_review(payload, {"uid": self.client_user.firebase_uid}, self.db)
        self.assertEqual(res["resena"].puntuacion, 5)

        with self.assertRaises(HTTPException) as ctx:
            create_service_review(payload, {"uid": self.client_user.firebase_uid}, self.db)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_hu46_double_blind_and_bilateral_simultaneous_publication(self):
        t_client = self.completion_time + timedelta(hours=2)
        payload_client = CreateReviewSchema(
            tipo_solicitud="DIRECTA",
            solicitud_id=str(self.direct_req.id),
            puntuacion=5,
            comentario="Muy profesional y limpio",
            aspectos_destacados=["Confiable", "Puntual"],
        )
        res1 = create_service_review(
            payload_client,
            {"uid": self.client_user.firebase_uid},
            self.db,
            clock_fn=lambda: t_client,
        )
        self.assertEqual(res1["estado_publicacion"], "PENDIENTE")
        self.assertFalse(res1["publicada_inmediata"])

        rep_tech_initial = get_user_reputation_summary(
            str(self.tech_user.id), self.db, clock_fn=lambda: t_client
        )
        self.assertIsNone(rep_tech_initial.promedio)
        self.assertEqual(rep_tech_initial.total_resenas, 0)
        self.assertTrue(rep_tech_initial.sin_calificacion)

        status_tech_view = get_service_reviews_status_data(
            "DIRECTA",
            str(self.direct_req.id),
            {"uid": self.tech_user.firebase_uid},
            self.db,
            clock_fn=lambda: t_client,
        )
        self.assertIsNone(status_tech_view.contraparte_resena)
        self.assertTrue(status_tech_view.contraparte_ha_calificado)

        t_tech = self.completion_time + timedelta(hours=5)
        payload_tech = CreateReviewSchema(
            tipo_solicitud="DIRECTA",
            solicitud_id=str(self.direct_req.id),
            puntuacion=4,
            comentario="Excelente cliente, trato muy amable",
            aspectos_destacados=["Trato amable", "Pago puntual"],
        )
        res2 = create_service_review(
            payload_tech,
            {"uid": self.tech_user.firebase_uid},
            self.db,
            clock_fn=lambda: t_tech,
        )
        self.assertEqual(res2["estado_publicacion"], "PUBLICADA")
        self.assertTrue(res2["publicada_inmediata"])

        client_rev_db = self.db.scalar(
            select(ServiceReview).where(
                ServiceReview.solicitud_id == str(self.direct_req.id),
                ServiceReview.rol_evaluador == "CLIENTE",
            )
        )
        self.assertIsNotNone(client_rev_db)
        self.assertEqual(client_rev_db.estado_publicacion, "PUBLICADA")
        self.assertIsNotNone(client_rev_db.publicada_en)

        rep_tech = get_user_reputation_summary(str(self.tech_user.id), self.db)
        self.assertEqual(rep_tech.promedio, 5.0)
        self.assertEqual(rep_tech.total_resenas, 1)

        rep_client = get_user_reputation_summary(str(self.client_user.id), self.db)
        self.assertEqual(rep_client.promedio, 4.0)
        self.assertEqual(rep_client.total_resenas, 1)

    def test_hu46_silent_expiration_after_48_hours_single_review(self):
        t_client = self.completion_time + timedelta(hours=2)
        payload_client = CreateReviewSchema(
            tipo_solicitud="DIRECTA",
            solicitud_id=str(self.direct_req.id),
            puntuacion=5,
            comentario="Gran trabajo en los tomacorrientes",
        )
        res1 = create_service_review(
            payload_client,
            {"uid": self.client_user.firebase_uid},
            self.db,
            clock_fn=lambda: t_client,
        )
        self.assertEqual(res1["estado_publicacion"], "PENDIENTE")

        t_40h = self.completion_time + timedelta(hours=40)
        sync_40h = sync_expired_reviews(self.db, clock_fn=lambda: t_40h)
        self.assertEqual(sync_40h, 0)
        rep_40h = get_user_reputation_summary(str(self.tech_user.id), self.db, clock_fn=lambda: t_40h)
        self.assertIsNone(rep_40h.promedio)

        t_49h = self.completion_time + timedelta(hours=49)
        sync_49h = sync_expired_reviews(self.db, clock_fn=lambda: t_49h)
        self.assertEqual(sync_49h, 1)

        sync_repeat = sync_expired_reviews(self.db, clock_fn=lambda: t_49h)
        self.assertEqual(sync_repeat, 0)

        rep_49h = get_user_reputation_summary(str(self.tech_user.id), self.db, clock_fn=lambda: t_49h)
        self.assertEqual(rep_49h.promedio, 5.0)
        self.assertEqual(rep_49h.total_resenas, 1)
        self.assertFalse(rep_49h.sin_calificacion)

    def test_hu46_average_calculation_4_and_5_equals_4_point_5(self):
        second_direct_req = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.client_user.firebase_uid,
            tecnico_id=self.tech_user.firebase_uid,
            servicio_id=self.service.id,
            municipio="Tegucigalpa",
            sector="Palmira",
            direccion_exacta="Calle 3",
            fecha_hora=self.completion_time,
            descripcion="Segunda solicitud",
            cantidad=1.0,
            tarifa_aplicada=350.0,
            unidad_cobro="POR_HORA",
            monto_base=350.0,
            metodo_pago="efectivo",
            estado="COMPLETADA",
            creada_en=self.completion_time,
            seguimiento_recepcion={"completada": True, "fecha_cierre": self.completion_time.isoformat()},
        )
        self.db.add(second_direct_req)
        self.db.commit()

        r1 = ServiceReview(
            tipo_solicitud="DIRECTA",
            solicitud_id=str(self.direct_req.id),
            evaluador_id=self.client_user.id,
            evaluado_id=self.tech_user.id,
            rol_evaluador="CLIENTE",
            puntuacion=4,
            comentario="Buen servicio",
            estado_publicacion="PUBLICADA",
            fecha_completado_servicio=self.completion_time,
            creada_en=self.completion_time,
            publicada_en=self.completion_time,
        )
        r2 = ServiceReview(
            tipo_solicitud="DIRECTA",
            solicitud_id=str(second_direct_req.id),
            evaluador_id=self.client_user.id,
            evaluado_id=self.tech_user.id,
            rol_evaluador="CLIENTE",
            puntuacion=5,
            comentario="Excelente trabajo",
            estado_publicacion="PUBLICADA",
            fecha_completado_servicio=self.completion_time,
            creada_en=self.completion_time,
            publicada_en=self.completion_time,
        )
        self.db.add_all([r1, r2])
        self.db.commit()

        rep = get_user_reputation_summary(str(self.tech_user.id), self.db)
        self.assertEqual(rep.promedio, 4.5)
        self.assertEqual(rep.total_resenas, 2)
        self.assertFalse(rep.sin_calificacion)
        self.assertEqual(rep.distribucion_estrellas["4"], 1)
        self.assertEqual(rep.distribucion_estrellas["5"], 1)

    def test_open_request_completion_and_rating_flow(self):
        open_req = OpenRequest(
            id=f"open-{uuid.uuid4()}",
            cliente_id=self.client_user.firebase_uid,
            tecnico_id=self.tech_user.firebase_uid,
            especialidad_id=self.spec.id,
            servicio_id=self.service.id,
            modalidad="ABIERTA",
            municipio="Tegucigalpa",
            sector="Miraflores",
            direccion_exacta="Bloque 12",
            fecha_hora=self.completion_time,
            descripcion="Reparación abierta",
            presupuesto=800.0,
            monto_base=800.0,
            metodo_pago="efectivo",
            estado="COMPLETADA",
            seguimiento_recepcion={"completada": True, "fecha_cierre": self.completion_time.isoformat()},
        )
        self.db.add(open_req)
        self.db.commit()

        payload = CreateReviewSchema(
            tipo_solicitud="ABIERTA",
            solicitud_id=str(open_req.id),
            puntuacion=5,
            comentario="Excelente licitación y trabajo",
        )
        res = create_service_review(payload, {"uid": self.client_user.firebase_uid}, self.db)
        self.assertEqual(res["resena"].tipo_solicitud, "ABIERTA")
        self.assertEqual(res["resena"].solicitud_id, str(open_req.id))

    def test_e2e_hu34_hu35_completion_feeds_real_closing_timestamp_to_reviews(self):
        t0 = datetime(2026, 11, 20, 14, 0, 0, tzinfo=timezone.utc)
        req = DirectRequest(
            id=str(uuid.uuid4()),
            cliente_id=self.client_user.firebase_uid,
            tecnico_id=self.tech_user.firebase_uid,
            servicio_id=self.service.id,
            municipio="Distrito Central",
            sector="Kennedy",
            direccion_exacta="Bloque K #5",
            fecha_hora=t0,
            descripcion="Reparación de tablero",
            cantidad=1.0,
            tarifa_aplicada=350.0,
            unidad_cobro="POR_HORA",
            monto_base=350.0,
            metodo_pago="efectivo",
            estado="EN_PROCESO",
            creada_en=t0 - timedelta(hours=3),
        )
        self.db.add(req)
        self.db.commit()

        # 1. Técnico reporta trabajo finalizado (HU-34)
        report_service_completion(str(req.id), {"uid": self.tech_user.firebase_uid}, self.db, now=t0)

        # 2. Cliente confirma recepción 1 hora después (HU-35) -> pasa a COMPLETADA con fecha_cierre t_close
        t_close = t0 + timedelta(hours=1)
        confirm_res = confirm_service_reception_by_client(
            str(req.id),
            {"uid": self.client_user.firebase_uid},
            self.db,
            payload=ConfirmReceptionSchema(comentario="Todo en orden"),
            now=t_close,
        )
        self.assertEqual(confirm_res["estado"], "COMPLETADA")

        # 3. Verificar Adapter y fecha de cierre real
        adapter = DirectReviewableRequestAdapter()
        self.db.refresh(req)
        self.assertTrue(adapter.is_completed(req))
        self.assertEqual(adapter.resolve_completed_at(req, self.db), t_close)

        # 4. Técnico califica 1h después del cierre -> PENDIENTE respecto a t_close
        res_tech = create_service_review(
            CreateReviewSchema(
                tipo_solicitud="DIRECTA",
                solicitud_id=str(req.id),
                puntuacion=5,
                comentario="Cliente muy cordial",
            ),
            {"uid": self.tech_user.firebase_uid},
            self.db,
            clock_fn=lambda: t_close + timedelta(hours=1),
        )
        self.assertEqual(res_tech["estado_publicacion"], "PENDIENTE")

        # 5. A las 47h desde el reporte pero 46h desde el cierre real NO debe expirar (plazos independientes!)
        self.assertEqual(sync_expired_reviews(self.db, clock_fn=lambda: t0 + timedelta(hours=47)), 0)

        # 6. A las 48h desde el cierre real (`t_close + 48h`) expira silenciosamente y publica
        self.assertEqual(sync_expired_reviews(self.db, clock_fn=lambda: t_close + timedelta(hours=48)), 1)

    def test_adapter_and_strategy_contracts_solid(self):
        # Adapter LSP check for both Direct and Open
        direct_adapter = DirectReviewableRequestAdapter()
        open_adapter = OpenReviewableRequestAdapter()
        self.assertEqual(direct_adapter.tipo_solicitud, "DIRECTA")
        self.assertEqual(open_adapter.tipo_solicitud, "ABIERTA")
        self.assertIsNone(direct_adapter.find_request(self.db, "non-existent"))
        self.assertIsNone(open_adapter.find_request(self.db, "non-existent"))

        # Strategy OCP / DIP check
        dummy_review = ServiceReview(
            tipo_solicitud="DIRECTA",
            solicitud_id="x",
            evaluador_id=self.client_user.id,
            evaluado_id=self.tech_user.id,
            rol_evaluador="CLIENTE",
            puntuacion=5,
            comentario="Ok",
            fecha_completado_servicio=self.completion_time,
        )
        pub_bilateral, strat_a = evaluate_review_publication_policy(
            counterpart_review=dummy_review,
            completed_at=self.completion_time,
            now=self.completion_time + timedelta(minutes=5),
            strategies=(BilateralMutualPublicationStrategy(), SilentTimeoutPublicationStrategy()),
        )
        self.assertTrue(pub_bilateral)
        self.assertEqual(strat_a, "BILATERAL_SIMULTANEA")

        pub_timeout, strat_b = evaluate_review_publication_policy(
            counterpart_review=None,
            completed_at=self.completion_time,
            now=self.completion_time + timedelta(hours=48),
            strategies=(BilateralMutualPublicationStrategy(), SilentTimeoutPublicationStrategy()),
        )
        self.assertTrue(pub_timeout)
        self.assertEqual(strat_b, "EXPIRACION_SILENCIOSA_48H")


if __name__ == "__main__":
    unittest.main()
