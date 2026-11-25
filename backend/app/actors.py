"""Resolución de actores autenticados (compartido por los módulos HU-36)."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User


def require_user(db: Session, claims: dict) -> User:
    uid = claims.get("uid") if isinstance(claims, dict) else None
    user = db.scalar(select(User).where(User.firebase_uid == uid)) if uid else None
    if user is None:
        raise HTTPException(401, "Sesión inválida o expirada")
    return user


def require_admin(db: Session, claims: dict) -> User:
    user = require_user(db, claims)
    if not any(role.rol == "ADMIN" for role in user.roles):
        raise HTTPException(403, "Solo administradores autorizados pueden realizar esta acción.")
    return user