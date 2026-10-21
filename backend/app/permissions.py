"""Rules to reuse in future hiring endpoints; never trust frontend state."""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import TechnicianProfile, User


def require_verified_technician(user: User, db: Session) -> TechnicianProfile:
    if not any(role.rol == "TECNICO" for role in user.roles):
        raise HTTPException(403, "La cuenta no tiene rol de técnico")
    technician = db.get(TechnicianProfile, user.id)
    if (user.estado_identidad != "VERIFICADO" or technician is None
            or technician.estado_verificacion != "VERIFICADO"):
        raise HTTPException(403, "El técnico debe completar su verificación de identidad")
    return technician
