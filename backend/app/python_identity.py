"""Adapter to an isolated local Python ML process; no external identity API."""
import base64
import json
import os
from pathlib import Path
import subprocess
import threading

from app.identity_processing import IdentityChecks

_INFERENCE_LOCK = threading.Lock()


class PythonIdentityProcessor:
    name = 'PYTHON_LOCAL_OCR_FACE'
    version = 'paddle3-facenet512-opencv-2'

    def evaluate(self, *, dni: str, nombre_completo: str, documents: dict[str, bytes]) -> IdentityChecks:
        root = Path(__file__).resolve().parents[1]
        default_python = root / '.venv-identity' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        python = Path(os.getenv('IDENTITY_PYTHON', str(default_python)))
        if not python.is_file():
            raise RuntimeError('Local identity runtime is not installed')
        payload = {
            'dni': dni, 'nombre_completo': nombre_completo,
            'documents': {kind: base64.b64encode(data).decode('ascii') for kind, data in documents.items()},
        }
        environment = os.environ.copy()
        # Provider libraries must not inherit database, Firebase or storage secrets.
        for key in list(environment):
            if any(part in key.upper() for part in ('SECRET', 'TOKEN', 'PASSWORD', 'API_KEY', 'SERVICE_ROLE', 'DATABASE_URL', 'GOOGLE_APPLICATION_CREDENTIALS')):
                environment.pop(key, None)
        environment.update({
            'PYTHONIOENCODING': 'utf-8', 'TF_CPP_MIN_LOG_LEVEL': '3',
            'DEEPFACE_HOME': str(root / '.identity_models'),
            'PADDLE_PDX_CACHE_HOME': str(root / '.identity_models' / 'paddle'),
        })
        # Avoid simultaneous heavyweight jobs in the same API process.
        if not _INFERENCE_LOCK.acquire(blocking=False):
            raise RuntimeError('Another identity evaluation is in progress')
        try:
            completed = subprocess.run(
                [str(python), '-m', 'app.identity_worker'],
                cwd=root, input=json.dumps(payload), text=True, encoding='utf-8',
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env=environment, timeout=180, check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
        finally:
            _INFERENCE_LOCK.release()
        if completed.returncode != 0 or len(completed.stdout) > 4096:
            raise RuntimeError('Local identity evaluation failed')
        response = json.loads(completed.stdout)
        diagnostic = response.get('diagnostico_facial', {})
        if not isinstance(diagnostic, dict) or any(
            not isinstance(item, dict) or item.get('estado') in ('ERROR_DETECCION', 'ERROR_COMPARACION')
            for item in diagnostic.values()
        ):
            raise RuntimeError('Local face processing failed')
        self.face_diagnostic = diagnostic
        models = response.get('modelos', {})
        if not isinstance(models, dict) or any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 100 for k, v in models.items()):
            raise ValueError('Invalid model versions')
        self.model_versions = models
        values = response.get('controles')
        if not isinstance(values, dict) or set(values) != set(IdentityChecks.__dataclass_fields__):
            raise ValueError('Invalid local identity response')
        if any(value is not None and type(value) is not bool for value in values.values()):
            raise ValueError('Invalid local identity control')
        # OCR + facial matching do not establish either of these controls.
        values['documento_autentico'] = None
        values['vida_confirmada'] = None
        return IdentityChecks(**values)
