from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth

bearer = HTTPBearer(auto_error=False)
# Allow small clock differences between Firebase and the API host.
TOKEN_CLOCK_SKEW_SECONDS = 5


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
):
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Se requiere un token Bearer",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        return auth.verify_id_token(
            credentials.credentials,
            check_revoked=True,
            clock_skew_seconds=TOKEN_CLOCK_SKEW_SECONDS,
        )
    except (
        auth.InvalidIdTokenError,
        auth.ExpiredIdTokenError,
        auth.RevokedIdTokenError,
        auth.UserDisabledError,
        ValueError,
    ):
        raise HTTPException(
            status_code=401,
            detail="Token inválido, vencido o revocado",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
