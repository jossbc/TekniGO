"""Local ML worker. Sensitive images stay in memory; stdout is a small JSON result."""
import base64
from contextlib import redirect_stdout
import io
import json
import math
from importlib.metadata import version
import os
from pathlib import Path
import sys

from app.identity_text import compare_document_text


def load_models(*, debug=False):
    # Shared by explicit warm-up and actual evaluation.
    root = Path(__file__).resolve().parents[1]
    os.environ.setdefault('DEEPFACE_HOME', str(root / '.identity_models'))
    os.environ.setdefault('PADDLE_PDX_CACHE_HOME', str(root / '.identity_models' / 'paddle'))
    os.environ.setdefault('PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK', 'True')
    os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '3')
    def stage(message):
        if debug:
            print(message, file=sys.stderr, flush=True)

    # PaddleOCR imports its native Paddle runtime lazily. Initialize it before
    # TensorFlow so the actual native-library load order matches the import order.
    stage('Modelos: importando núcleo Paddle...')
    import paddle
    stage('Modelos: importando PaddleOCR...')
    from paddleocr import PaddleOCR
    stage('Modelos: creando OCR...')
    ocr = PaddleOCR(lang='es', ocr_version='PP-OCRv5', use_doc_orientation_classify=False,
                    use_doc_unwarping=False, use_textline_orientation=False, enable_mkldnn=False)
    stage('Modelos: importando DeepFace/TensorFlow...')
    from deepface import DeepFace
    stage('Modelos: creando Facenet512...')
    DeepFace.build_model('Facenet512')
    stage('Modelos: preparación completada.')
    return ocr, DeepFace


def compare_faces(images, deepface, *, not_detected_error):
    """Return only counts/statuses; never log pixels, embeddings or exception text."""
    diagnostic = {}
    selected = {}
    for kind in ('DNI_FRONTAL', 'SELFIE'):
        try:
            faces = deepface.extract_faces(img_path=images[kind], detector_backend='opencv',
                                           enforce_detection=True, align=True,
                                           color_face='bgr', normalize_face=False)
            count = len(faces)
            diagnostic[kind] = {'rostros_detectados': count,
                                'estado': 'OK' if count == 1 else 'SIN_ROSTRO' if count == 0 else 'MULTIPLES_ROSTROS'}
            if count == 1:
                selected[kind] = faces[0]['face']
                diagnostic[kind]['seleccion'] = 'ROSTRO_UNICO'
            elif kind == 'DNI_FRONTAL' and count == 2:
                # Provisional document-portrait heuristic, not an authenticity check.
                # Never pick whichever face happens to match the selfie best.
                areas = [float(face.get('facial_area', {}).get('w', 0)) *
                         float(face.get('facial_area', {}).get('h', 0)) for face in faces]
                if all(math.isfinite(area) and area > 0 for area in areas):
                    largest = 0 if areas[0] > areas[1] else 1
                    ratio = areas[largest] / areas[1 - largest]
                    diagnostic[kind]['proporcion_areas'] = round(ratio, 2)
                    if ratio >= 3:
                        selected[kind] = faces[largest]['face']
                        diagnostic[kind]['estado'] = 'OK'
                        diagnostic[kind]['seleccion'] = 'RETRATO_PRINCIPAL_POR_TAMANO'
        except not_detected_error:
            diagnostic[kind] = {'rostros_detectados': 0, 'estado': 'SIN_ROSTRO'}
        except Exception as error:
            diagnostic[kind] = {'rostros_detectados': None, 'estado': 'ERROR_DETECCION',
                                'tipo_error': type(error).__name__}
    if any(item['estado'] != 'OK' for item in diagnostic.values()):
        diagnostic['comparacion'] = {'estado': 'OMITIDA'}
        return None, diagnostic
    try:
        result = deepface.verify(
            img1_path=selected['DNI_FRONTAL'], img2_path=selected['SELFIE'],
            # Detection/alignment already ran above; compare exactly the selected crops.
            model_name='Facenet512', detector_backend='skip', enforce_detection=True,
            align=False, anti_spoofing=False,
        )
        if not isinstance(result, dict) or type(result.get('verified')) is not bool:
            diagnostic['comparacion'] = {'estado': 'ERROR_COMPARACION', 'tipo_error': 'RespuestaInvalida'}
            return None, diagnostic
        diagnostic['comparacion'] = {'estado': 'COINCIDE' if result['verified'] else 'NO_COINCIDE'}
        for key, label in (('distance', 'distancia'), ('threshold', 'umbral')):
            value = result.get(key)
            if type(value) in (int, float) and math.isfinite(value):
                diagnostic['comparacion'][label] = round(value, 6)
        return result['verified'], diagnostic
    except Exception as error:
        diagnostic['comparacion'] = {'estado': 'ERROR_COMPARACION', 'tipo_error': type(error).__name__}
        return None, diagnostic


def evaluate(payload):
    import cv2
    import numpy as np
    ocr, DeepFace = load_models()
    from deepface.modules.exceptions import FaceNotDetected

    images = {}
    for kind in ('DNI_FRONTAL', 'DNI_REVERSO', 'SELFIE'):
        raw = base64.b64decode(payload['documents'][kind], validate=True)
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape[0] < 100 or image.shape[1] < 100 or image.shape[0] * image.shape[1] > 25_000_000:
            raise ValueError('Image cannot be evaluated')
        # Bound inference dimensions; no unencrypted temporary image files.
        scale = min(1, 1800 / max(image.shape[:2]))
        if scale < 1:
            image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        images[kind] = image
    text = []
    readable_sides = []
    for kind in ('DNI_FRONTAL', 'DNI_REVERSO'):
        side = []
        for result in ocr.predict(images[kind]):
            for line, score in zip(result['rec_texts'], result['rec_scores'], strict=True):
                side.append((str(line), float(score)))
        readable_sides.append(any(score >= 0.90 for _, score in side))
        text.extend(side)
    legible, matches = compare_document_text(text, dni=payload['dni'], nombre_completo=payload['nombre_completo'])
    if not all(readable_sides):
        legible, matches = None, None
    facial_match, diagnostic = compare_faces(images, DeepFace, not_detected_error=FaceNotDetected)
    return {'diagnostico_facial': diagnostic, 'modelos': {
        'ocr': 'PP-OCRv5/es', 'rostro': 'Facenet512', 'detector': 'opencv',
        **{package: version(package) for package in ('paddleocr', 'paddlepaddle', 'deepface', 'tensorflow')},
    }, 'controles': {
        'documento_legible': legible, 'datos_coinciden': matches,
        'rostro_coincide': facial_match, 'documento_autentico': None, 'vida_confirmada': None,
    }}


def check_local():
    """Interactive diagnostic on user-selected originals; no DB or Storage access."""
    print('Prueba local: usa imágenes originales JPG, PNG o WEBP, no archivos .enc.')
    documents = {}
    try:
        for kind, label in (('DNI_FRONTAL', 'DNI frontal'), ('DNI_REVERSO', 'DNI reverso'), ('SELFIE', 'selfie')):
            path = Path(input(f'Ruta de {label}: ').strip().strip('"').strip("'")).expanduser()
            if path.suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp') or not path.is_file():
                print('La ruta debe corresponder a una imagen JPG, PNG o WEBP existente.')
                return 1
            if path.stat().st_size > 5 * 1024 * 1024:
                print('La imagen supera el límite de 5 MB.')
                return 1
            documents[kind] = base64.b64encode(path.read_bytes()).decode('ascii')
        dni = input('DNI esperado (13 dígitos, sin guiones): ').strip()
        name = input('Nombre completo esperado: ').strip()
        if len(dni) != 13 or not dni.isascii() or not dni.isdigit() or len(name.split()) < 2:
            print('Escribe un DNI de 13 dígitos y un nombre completo.')
            return 1
        print('Evaluando las imágenes localmente; puede tardar unos minutos…')
        with redirect_stdout(io.StringIO()):
            result = evaluate({'dni': dni, 'nombre_completo': name, 'documents': documents})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print('true = coincidencia; false = discrepancia; null = no concluyente o control no implementado.')
        print('Esta prueba no aprueba identidades ni guarda resultados en la base de datos.')
        return 0
    except (EOFError, KeyboardInterrupt):
        print('\nPrueba cancelada.')
    except Exception as error:
        print(f'La prueba local no se completó ({type(error).__name__}). No se guardaron datos.')
    return 1


def main():
    if sys.argv[1:] == ['--check-local']:
        return check_local()
    if sys.argv[1:] in (['--prepare'], ['--prepare', '--debug']):
        try:
            with redirect_stdout(io.StringIO()):
                load_models(debug='--debug' in sys.argv)
        except Exception as error:
            if isinstance(error, ModuleNotFoundError):
                print(f'Falta una dependencia del entorno de identidad: {error.name}.')
            else:
                print('No se pudieron preparar los modelos. Ejecuta --prepare --debug para ver el error de preparación.')
            if '--debug' in sys.argv:
                # Explicit model warm-up only: no identity documents are read here.
                import traceback
                traceback.print_exc()
            return 1
        print('OK: modelos de OCR y comparación facial preparados.')
        return 0
    payload = json.loads(sys.stdin.read(24 * 1024 * 1024))
    try:
        # Model libraries can print diagnostics; never include them in the API response.
        with redirect_stdout(io.StringIO()):
            response = evaluate(payload)
    except Exception:
        return 1
    sys.stdout.write(json.dumps(response))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
