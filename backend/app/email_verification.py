"""Email ownership comes from Firebase, never from a client-supplied flag."""
from fastapi import APIRouter, Depends, HTTPException
from firebase_admin import auth, exceptions
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security import get_current_user

router = APIRouter()


def email_verification_status(user: User) -> dict:
    try:
        identity = auth.get_user(user.firebase_uid)
    except auth.UserNotFoundError:
        raise HTTPException(401, "Tu cuenta de acceso ya no está disponible") from None
    except exceptions.FirebaseError:
        raise HTTPException(503, "No pudimos comprobar tu correo. Intenta nuevamente.") from None
    if identity.disabled:
        raise HTTPException(401, "Tu cuenta de acceso está deshabilitada")
    # A cached token for a previous address must never approve the new address.
    matches = bool(identity.email) and identity.email.casefold() == user.correo.casefold()
    return {"email": user.correo, "email_verified": matches and identity.email_verified is True}


def require_verified_email(user: User) -> None:
    """Reusable server-side guard for future hiring endpoints."""
    if not email_verification_status(user)["email_verified"]:
        raise HTTPException(403, "Verifica tu correo electrónico para continuar")


@router.get("/users/me/email-verification")
def get_email_verification(claims=Depends(get_current_user), db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.firebase_uid == claims["uid"]))
    if not user:
        raise HTTPException(404, "Completa tu registro en TekniGO")
    return email_verification_status(user)
