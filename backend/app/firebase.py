from pathlib import Path

import firebase_admin
from firebase_admin import credentials


def initialize_firebase():
    if firebase_admin.get_app:
        try:
            return firebase_admin.get_app()
        except ValueError:
            pass

    credential_path = (
        Path(__file__).resolve().parent.parent
        / "secrets"
        / "firebase-admin.json"
    )

    credential = credentials.Certificate(str(credential_path))
    return firebase_admin.initialize_app(credential)