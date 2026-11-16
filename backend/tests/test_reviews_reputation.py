import unittest
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.main import app as fastapi_app
from app.models import (
    DirectRequest,
    OpenRequest,
    Service,
    ServiceReview,
    Specialty,
    TechnicianProfile,
    User,
    UserRole,
)
from app.reviews import (
    CreateReviewSchema,
    create_service_review,
    get_service_reviews_status_data,
    get_user_reputation_summary,
    list_user_published_reviews,
    sync_expired_reviews,
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

        # Solicitud directa completada
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
            completada_en=self.completion_time,
        )
        self.db.add(self.direct_req)
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
        # 1. Servicio pendiente no se puede calificar (400)
        self.direct_req.estado = "PENDIENTE"
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
        # Puntuación menor a 1 o mayor a 5 rechazada
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
        # Comentario vacío rechazado
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

        # Reintento por el mismo cliente arroja 409 Conflict
        with self.assertRaises(HTTPException) as ctx:
            create_service_review(payload, {"uid": self.client_user.firebase_uid}, self.db)
        self.assertEqual(ctx.exception.status_code, 409)

    def test_hu46_double_blind_and_bilateral_simultaneous_publication(self):
        # Paso 1: Cliente califica primero dentro de las 48h (T_completed + 2h)
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
        # CA-04 HU-44: Queda PENDIENTE
        self.assertEqual(res1["estado_publicacion"], "PENDIENTE")
        self.assertFalse(res1["publicada_inmediata"])

        # Para el perfil público del técnico, el promedio y total siguen siendo None y 0 (Doble ciego)
        rep_tech_initial = get_user_reputation_summary(
            str(self.tech_user.id), self.db, clock_fn=lambda: t_client
        )
        self.assertIsNone(rep_tech_initial.promedio)
        self.assertEqual(rep_tech_initial.total_resenas, 0)
        self.assertTrue(rep_tech_initial.sin_calificacion)

        # Al consultar el estado del servicio desde el lado del técnico, la reseña del cliente está oculta
        status_tech_view = get_service_reviews_status_data(
            "DIRECTA",
            str(self.direct_req.id),
            {"uid": self.tech_user.firebase_uid},
            self.db,
            clock_fn=lambda: t_client,
        )
        self.assertIsNone(status_tech_view.contraparte_resena)
        self.assertTrue(status_tech_view.contraparte_ha_calificado)

        # Paso 2: Técnico califica (HU-45 y HU-46 CA-01: Publicación bilateral simultánea)
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

        # Verificar que la reseña del cliente ahora también pasó a PUBLICADA
        client_rev_db = self.db.scalar(
            self.db.query(ServiceReview).filter_by(
                solicitud_id=str(self.direct_req.id), rol_evaluador="CLIENTE"
            ).statement
        )
        self.assertEqual(client_rev_db.estado_publicacion, "PUBLICADA")
        self.assertIsNotNone(client_rev_db.publicada_en)

        # Ambas reputaciones ahora reflejan las calificaciones publicadas
        # Técnico recibió un 5 -> promedio 5.0
        rep_tech = get_user_reputation_summary(str(self.tech_user.id), self.db)
        self.assertEqual(rep_tech.promedio, 5.0)
        self.assertEqual(rep_tech.total_resenas, 1)

        # Cliente recibió un 4 -> promedio 4.0
        rep_client = get_user_reputation_summary(str(self.client_user.id), self.db)
        self.assertEqual(rep_client.promedio, 4.0)
        self.assertEqual(rep_client.total_resenas, 1)

    def test_hu46_silent_expiration_after_48_hours_single_review(self):
        # 1. Cliente califica al técnico a las 2 horas de completado
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

        # A las 40 horas sigue PENDIENTE
        t_40h = self.completion_time + timedelta(hours=40)
        sync_40h = sync_expired_reviews(self.db, clock_fn=lambda: t_40h)
        self.assertEqual(sync_40h, 0)
        rep_40h = get_user_reputation_summary(str(self.tech_user.id), self.db, clock_fn=lambda: t_40h)
        self.assertIsNone(rep_40h.promedio)

        # A las 48.1 horas se publica silenciosamente (CA-01, CA-02, CA-03)
        t_49h = self.completion_time + timedelta(hours=49)
        sync_49h = sync_expired_reviews(self.db, clock_fn=lambda: t_49h)
        self.assertEqual(sync_49h, 1)

        # Idempotencia: segunda llamada no publica nada extra
        sync_repeat = sync_expired_reviews(self.db, clock_fn=lambda: t_49h)
        self.assertEqual(sync_repeat, 0)

        # La reputación del técnico ahora se muestra pública con promedio 5.0
        rep_49h = get_user_reputation_summary(str(self.tech_user.id), self.db, clock_fn=lambda: t_49h)
        self.assertEqual(rep_49h.promedio, 5.0)
        self.assertEqual(rep_49h.total_resenas, 1)
        self.assertFalse(rep_49h.sin_calificacion)

    def test_hu46_average_calculation_4_and_5_equals_4_point_5(self):
        # Creamos una segunda solicitud directa completada para tener 2 evaluaciones hacia el técnico
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
            completada_en=self.completion_time,
        )
        self.db.add(second_direct_req)
        self.db.commit()

        # Reseña 1: puntuación 4 (publicada)
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
        # Reseña 2: puntuación 5 (publicada)
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
        # Promedio con 4 y 5 debe ser exactamente 4.5
        self.assertEqual(rep.promedio, 4.5)
        self.assertEqual(rep.total_resenas, 2)
        self.assertFalse(rep.sin_calificacion)
        self.assertEqual(rep.distribucion_estrellas["4"], 1)
        self.assertEqual(rep.distribucion_estrellas["5"], 1)

    def test_open_request_completion_and_rating_flow(self):
        # HU-44 / HU-45 también funciona para solicitudes abiertas (ABIERTA)
        open_req = OpenRequest(
            id=str(uuid.uuid4()),
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
            completada_en=self.completion_time,
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


if __name__ == "__main__":
    unittest.main()
