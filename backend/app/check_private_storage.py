"""Opt-in live check using a synthetic image; never reads identity records."""
import argparse
import base64
import json
import urllib.error
import urllib.request
import uuid

from fastapi import HTTPException

# Load the same .env used by FastAPI without opening a database connection.
from app import database  # noqa: F401
from app.storage import PRIVATE_BUCKET, PrivateDocumentStorage, _key_material, _private_urlopen

IMAGE = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII='
)


def check(*, live: bool = False) -> int:
    try:
        _key_material()
        storage = PrivateDocumentStorage()
        config = storage._supabase_config()
        if config is None:
            print('ERROR: esta comprobación requiere Supabase, no almacenamiento local.')
            return 1
        print('OK: configuración local válida.')
        if not live:
            print('Para probar subida, lectura y limpieza remotas: añade --live.')
            return 0
        url, key = config
        headers = {'Authorization': f'Bearer {key}', 'apikey': key}
        with _private_urlopen(urllib.request.Request(
            f'{url}/storage/v1/bucket/{PRIVATE_BUCKET}', headers=headers,
        )) as response:
            bucket = json.load(response)
        if bucket.get('id') != PRIVATE_BUCKET or bucket.get('public') is not False:
            print('ERROR: no se confirmó que el bucket sea privado. No se subió nada.')
            return 1
        print('OK: bucket privado confirmado.')
        saved = storage.store(user_id=uuid.uuid4(), doc_type='SELFIE', content=IMAGE,
                              declared_content_type='image/png', label='imagen sintética de prueba')
        print('OK: imagen sintética subida y cifrada.')
        success = False
        try:
            if storage.read_private(saved.ruta_privada) != IMAGE:
                print('ERROR: el archivo recuperado no coincide con el original.')
            else:
                success = True
                print('OK: lectura privada y descifrado coinciden con el original.')
        finally:
            # Only remove the fresh random object returned by this check. No DB writes.
            object_key = saved.ruta_privada.removeprefix(f'private://{PRIVATE_BUCKET}/')
            try:
                request = urllib.request.Request(
                    f'{url}/storage/v1/object/{PRIVATE_BUCKET}', method='DELETE',
                    headers={**headers, 'Content-Type': 'application/json'},
                    data=json.dumps({'prefixes': [object_key]}).encode(),
                )
                with _private_urlopen(request) as response:
                    if response.status != 200:
                        raise OSError('Cleanup failed')
                print('OK: archivo de prueba eliminado.')
            except (urllib.error.URLError, TimeoutError, OSError):
                success = False
                print('ATENCIÓN: no se confirmó la limpieza. Elimina solo este objeto de prueba:')
                print(object_key)
        return 0 if success else 1
    except HTTPException as error:
        print('ERROR:', error.detail)
    except urllib.error.HTTPError as error:
        print('ERROR: Storage devolvió HTTP', error.code)
        error.close()
    except (urllib.error.URLError, TimeoutError, OSError):
        print('ERROR: no se pudo conectar con Supabase desde este proceso.')
    except (ValueError, KeyError):
        print('ERROR: respuesta de almacenamiento inválida.')
    return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Sube y elimina una imagen sintética en Supabase')
    raise SystemExit(check(live=parser.parse_args().live))
