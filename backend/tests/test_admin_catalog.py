import os
import unittest
from decimal import Decimal
from unittest.mock import patch
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.admin_catalog import (
    CategoryInput, SettingsInput, SuggestionDecision, TariffInput, create_category, get_catalog, get_history,
    review_suggestion, save_settings, save_tariff, seed_demo_settings, update_category,
)
from app.database import Base
from app.models import OperationalSettings, TechnicianProfile, User, UserRole
from app.open_requests import calculate_suggested_range
from app.specialties import SuggestionIn, list_specialties, my_suggestions, require_active_specialty, suggest_specialty


class AdminCatalogTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://")
        self.addCleanup(engine.dispose)
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.addCleanup(self.db.close)
        for suffix, role in enumerate(("ADMIN", "CLIENTE"), 1):
            user = User(firebase_uid=role, correo=f"{role}@example.com", nombre_completo=role,
                        dni=f"080120000000{suffix}", telefono=f"+5049000000{suffix}")
            self.db.add(user)
            self.db.flush()
            self.db.add(UserRole(usuario_id=user.id, rol=role))
        self.db.add(OperationalSettings(id=1))
        self.db.commit()
        self.admin = {"uid": "ADMIN"}

    def test_technician_suggestions_reach_the_admin_and_return_the_decision(self):
        technician = self.db.scalar(select(User).where(User.firebase_uid == "CLIENTE"))
        self.db.add(TechnicianProfile(usuario_id=technician.id))
        self.db.add(UserRole(usuario_id=technician.id, rol="TECNICO"))
        self.db.commit()
        claims = {"uid": "CLIENTE"}
        for name in ("Instalación solar", "Oficio de prueba"):
            suggest_specialty(SuggestionIn(nombre=name), claims, self.db)
        pending = {row["nombre"]: UUID(row["id"]) for row in get_catalog(self.admin, self.db)["sugerencias"]}
        approval = SuggestionDecision(estado="APROBADA")
        rejection = SuggestionDecision(estado="RECHAZADA")
        with self.assertRaises(HTTPException) as denied:
            review_suggestion(pending["Instalación solar"], approval, claims, self.db)
        self.assertEqual(denied.exception.status_code, 403)

        approve_id = pending["Instalación solar"]
        review_suggestion(approve_id, approval, self.admin, self.db)
        review_suggestion(approve_id, approval, self.admin, self.db)
        review_suggestion(pending["Oficio de prueba"], rejection, self.admin, self.db)
        self.assertEqual(get_catalog(self.admin, self.db)["sugerencias"], [])
        self.assertEqual([row["nombre"] for row in list_specialties(claims, self.db)], ["Instalación solar"])
        self.assertEqual({row["nombre"]: row["estado"] for row in my_suggestions(claims, self.db)}, {
            "Instalación solar": "APROBADA", "Oficio de prueba": "RECHAZADA",
        })
        self.assertEqual(sum(change["recurso"].startswith("sugerencia:") for change in get_history(self.admin, self.db)), 2)

    def test_category_permissions_normalized_duplicates_and_deactivation(self):
        with self.assertRaises(HTTPException) as denied:
            create_category(CategoryInput(nombre="Instalación solar"), {"uid": "CLIENTE"}, self.db)
        self.assertEqual(denied.exception.status_code, 403)
        created = create_category(CategoryInput(nombre="  Instalación   solar  "), self.admin, self.db)
        category_id = UUID(created["id"])
        with self.assertRaises(HTTPException) as duplicate:
            create_category(CategoryInput(nombre="INSTALACIÓN solar"), self.admin, self.db)
        self.assertEqual(duplicate.exception.status_code, 409)
        update_category(category_id, CategoryInput(nombre=created["nombre"], activa=False), self.admin, self.db)
        self.assertEqual(list_specialties(self.admin, self.db), [])
        with self.assertRaises(HTTPException):
            require_active_specialty(self.db, category_id)
        self.assertFalse(get_catalog(self.admin, self.db)["categorias"][0]["activa"])
        change = get_history(self.admin, self.db)[0]
        self.assertTrue(change["anterior"]["activa"])
        self.assertFalse(change["nuevo"]["activa"])
        self.assertEqual(change["administrador"], "ADMIN")
        self.assertTrue(change["fecha"])

    def test_suggested_average_overrides_range_with_explicit_rule(self):
        category = create_category(CategoryInput(nombre="Carpintería"), self.admin, self.db)
        category_id = UUID(category["id"])
        save_tariff(category_id, "POR_HORA", TariffInput(promedio=200), self.admin, self.db)
        result = calculate_suggested_range(category_id, self.db, unidad_cobro="POR_HORA", margin=Decimal("0.15"))
        self.assertEqual(result["origen_tarifa"], "CONFIGURADA")
        self.assertEqual(result["rango_sugerido_min"], 170)
        self.assertEqual(result["rango_sugerido_max"], 230)
        self.assertIn("15%", result["regla_calculo"])
        with self.assertRaises(ValidationError):
            TariffInput(promedio=0)

    def test_demo_seed_is_opt_in_and_updates_preserve_audit(self):
        with patch.dict(os.environ, {"TEKNIGO_DEMO": "false"}):
            with self.assertRaises(HTTPException) as denied:
                seed_demo_settings(self.admin, self.db)
            self.assertEqual(denied.exception.status_code, 403)
        with patch.dict(os.environ, {"TEKNIGO_DEMO": "true"}):
            demo = seed_demo_settings(self.admin, self.db)
            self.assertTrue(demo["valores_demo"])
            self.assertEqual(demo["comision_porcentaje"], "10.00")
            self.assertEqual(demo["limite_deuda"], "500.00")
            self.assertEqual(demo["minimo_retiro"], "200.00")
            self.assertEqual(demo["limite_sin_liquidar"], 5)
            with self.assertRaises(HTTPException) as already_set:
                seed_demo_settings(self.admin, self.db)
            self.assertEqual(already_set.exception.status_code, 409)
            update = SettingsInput(comision_porcentaje=12, limite_deuda=600, minimo_retiro=250)
            save_settings(update, self.admin, self.db)
        changes = get_history(self.admin, self.db)
        self.assertEqual(len(changes), 2)
        self.assertEqual(changes[0]["anterior"], changes[1]["nuevo"])
        self.assertEqual(changes[0]["nuevo"]["comision_porcentaje"], "12.00")
        for invalid in (
            {"comision_porcentaje": 101, "limite_deuda": 500, "minimo_retiro": 200},
            {"comision_porcentaje": 10, "limite_deuda": 0, "minimo_retiro": -1},
        ):
            with self.assertRaises(ValidationError):
                SettingsInput(**invalid)
