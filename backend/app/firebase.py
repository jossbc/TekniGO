import json
import os
from pathlib import Path

import firebase_admin
from firebase_admin import credentials


def initialize_firebase():
    try:
        return firebase_admin.get_app()
    except ValueError:
        pass

    firebase_admin_json = os.getenv("FIREBASE_ADMIN_CREDENTIALS_JSON")
    if firebase_admin_json:
        try:
            service_account = json.loads(firebase_admin_json)
        except ValueError:
            raise ValueError("FIREBASE_ADMIN_CREDENTIALS_JSON debe contener JSON válido.") from None
        if not isinstance(service_account, dict):
            raise ValueError("FIREBASE_ADMIN_CREDENTIALS_JSON debe contener un objeto JSON.")
        credential = credentials.Certificate(service_account)
        return firebase_admin.initialize_app(credential)

    if os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        return firebase_admin.initialize_app()
    credential_path = (
        Path(__file__).resolve().parent.parent
        / "secrets"
        / "firebase-admin.json"
    )

    credential = credentials.Certificate(str(credential_path))
    return firebase_admin.initialize_app(credential)
