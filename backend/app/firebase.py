from pathlib import Path
import os

import firebase_admin
from firebase_admin import credentials


def initialize_firebase():
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        return firebase_admin.initialize_app()
    credential_path = (
        Path(__file__).resolve().parent.parent
        / "secrets"
        / "firebase-admin.json"
    )

    credential = credentials.Certificate(str(credential_path))
    return firebase_admin.initialize_app(credential)
