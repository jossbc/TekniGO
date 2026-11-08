"""Firebase owns SMS verification; browsers cannot set verification flags."""
from fastapi import APIRouter, Depends, HTTPException
from firebase_admin import auth, exceptions
from sqlalchemy.orm import Session

from app.database import get_db
from app.identity_documents import _get_user
from app.models import TechnicianProfile, User
from app.security import get_current_user
from app.technician_activation import sync_technician_verification_state

router = APIRouter()


def phone_verification_status(user: User) -> dict:
    try:
        identity = auth.get_user(user.firebase_uid)
    except auth.UserNotFoundError:
        raise HTTPException(401, "Tu cuenta de acceso ya no está disponible") from None
    except exceptions.FirebaseError:
        raise HTTPException(503, "No pudimos comprobar tu teléfono. Intenta nuevamente.") from None
    if identity.disabled:
        raise HTTPException(401, "Tu cuenta de acceso está deshabilitada")
    return {
        "telefono": user.telefono,
        "telefono_verificado": bool(identity.phone_number) and identity.phone_number == user.telefono,
    }


@router.get("/users/me/phone-verification")
def get_phone_verification(claims=Depends(get_current_user), db: Session = Depends(get_db)):
    return phone_verification_status(_get_user(claims, db))


@router.post("/users/me/phone-verification")
def confirm_phone_verification(claims=Depends(get_current_user), db: Session = Depends(get_db)):
    user = _get_user(claims, db, lock=True)
    status = phone_verification_status(user)
    user.telefono_verificado = status["telefono_verificado"]
    technician = db.get(TechnicianProfile, user.id)
    if technician is not None:
        sync_technician_verification_state(
            user, technician, db, proceso_responsable="VERIFICACION_TELEFONO_FIREBASE",
            actor_usuario_id=user.id,
        )
    db.commit()
    return status
