"""Check local Firebase configuration without printing keys or contacting Firebase."""
import json
from pathlib import Path
import os

from dotenv import dotenv_values


def main():
    root = Path(__file__).resolve().parents[2]
    values = dotenv_values(root / "frontend" / ".env")
    required = ("VITE_FIREBASE_API_KEY", "VITE_FIREBASE_AUTH_DOMAIN",
                "VITE_FIREBASE_PROJECT_ID", "VITE_FIREBASE_APP_ID")
    missing = [key for key in required if not values.get(key)]
    if missing:
        print("Configuracion web pendiente:", ", ".join(missing))
        return 1
    backend_values = dotenv_values(root / "backend" / ".env")
    credential_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or backend_values.get("GOOGLE_APPLICATION_CREDENTIALS")
    credential = Path(credential_path) if credential_path else root / "backend" / "secrets" / "firebase-admin.json"
    if not credential.is_absolute():
        credential = root / "backend" / credential
    try:
        server_project = json.loads(credential.read_text(encoding="utf-8-sig")).get("project_id")
    except (OSError, ValueError):
        print("No se pudo comprobar el proyecto de la cuenta de servicio local.")
        return 1
    if values["VITE_FIREBASE_PROJECT_ID"] != server_project:
        print("Los proyectos Firebase del frontend y backend no coinciden.")
        return 1
    print("OK: campos web presentes y proyecto coincidente con el backend.")
    print("Pendiente de prueba en vivo: Email/Password, dominios y registro real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
