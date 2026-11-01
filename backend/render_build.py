"""Native Render build for identity testing. Does not migrate any database."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def run(arguments):
    subprocess.run(arguments, cwd=ROOT, check=True)


def main():
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError('Configura PYTHON_VERSION=3.13.5 en el servicio de prueba de Render')
    print('1/5: instalando dependencias de la API con Python 3.13…', flush=True)
    run([sys.executable, '-m', 'pip', 'install', '--no-cache-dir', '-r', 'requirements.txt'])
    print('2/5: creando el entorno de identidad…', flush=True)
    run([sys.executable, '-m', 'venv', '.venv-identity'])
    worker = ROOT / '.venv-identity' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    print('3/5: instalando OCR y comparación facial…', flush=True)
    run([str(worker), '-m', 'pip', 'install', '--no-cache-dir', '-r', 'requirements-identity-render.txt'])
    if sys.platform.startswith('linux'):
        # Upstream OCR/face dependencies install overlapping cv2 distributions.
        # Install the headless implementation last, so runtime needs no GUI/libGL.
        run([str(worker), '-m', 'pip', 'install', '--no-cache-dir', '--force-reinstall',
             '--no-deps', 'opencv-contrib-python-headless==4.10.0.84'])
    print('4/5: preparando modelos en el directorio de la aplicación…', flush=True)
    # Native crashes bypass Python exceptions; include the active Python stack.
    run([str(worker), '-X', 'faulthandler', '-m', 'app.identity_worker', '--prepare', '--debug'])
    print('5/5: ejecutando pruebas de la API…', flush=True)
    run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'])
    print('OK: construcción terminada. No se aplicaron migraciones ni se modificaron cuentas.', flush=True)


if __name__ == '__main__':
    main()
