"""Central server-side eligibility rules; never trust frontend flags."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.email_verification import email_verification_status
from app.models import TechnicianProfile, User
from app.security import get_current_user

router = APIRouter()


def client_hiring_eligibility(user: User) -> dict:
    """Current DB identity + live Firebase email ownership, independent of token claims."""
    if not any(role.rol == 'CLIENTE' for role in user.roles):
        raise HTTPException(403, 'La cuenta no tiene rol de cliente')
    identity_verified = user.estado_identidad == 'VERIFICADO'
    email_verified = email_verification_status(user)['email_verified']
    reasons = []
    if not identity_verified:
        reasons.append('Completa la verificación de identidad para contratar')
    if not email_verified:
        reasons.append('Verifica tu correo electrónico para contratar')
    return {
        'puede_contratar': identity_verified and email_verified,
        'identidad_verificada': identity_verified,
        'correo_verificado': email_verified,
        'motivos': reasons,
    }


def require_enabled_client(user: User) -> User:
    """Call inside every creation/confirmation path, including internal service calls."""
    eligibility = client_hiring_eligibility(user)
    if not eligibility['puede_contratar']:
        raise HTTPException(403, '. '.join(eligibility['motivos']))
    return user


def _registered_user(claims: dict, db: Session, *, lock: bool = False) -> User:
    query = select(User).where(User.firebase_uid == claims['uid'])
    if lock:
        query = query.with_for_update()
    user = db.scalar(query.execution_options(populate_existing=True))
    if user is None:
        raise HTTPException(404, 'Completa tu registro en TekniGO')
    return user


def get_enabled_client(claims=Depends(get_current_user), db: Session = Depends(get_db)) -> User:
    """Use Depends(get_enabled_client) for both creating AND confirming a hiring.

    The endpoint must share Depends(get_db) and commit its business operation
    in that session, so the identity row remains locked until the operation ends.
    """
    return require_enabled_client(_registered_user(claims, db, lock=True))


@router.get('/users/me/hiring-eligibility')
def get_my_hiring_eligibility(claims=Depends(get_current_user), db: Session = Depends(get_db)):
    # A read-only status check is not authorization for a later write. Recheck
    # through get_enabled_client in every hiring mutation.
    return client_hiring_eligibility(_registered_user(claims, db))


def require_verified_technician(user: User, db: Session) -> TechnicianProfile:
    if not any(role.rol == "TECNICO" for role in user.roles):
        raise HTTPException(403, "La cuenta no tiene rol de técnico")
    technician = db.get(TechnicianProfile, user.id)
    if (user.estado_identidad != "VERIFICADO" or technician is None
            or technician.estado_verificacion != "VERIFICADO"):
        raise HTTPException(403, "El técnico debe completar su verificación de identidad")
    return technician
