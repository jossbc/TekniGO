"""Private HTTP entry point for the existing identity worker."""
import asyncio
import base64
import hmac
import json
import os

from fastapi import FastAPI, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.python_identity import run_worker

MAX_DOCUMENT = 5 * 1024 * 1024
MAX_ENCODED_DOCUMENT = 4 * ((MAX_DOCUMENT + 2) // 3)
MAX_BODY = 3 * MAX_ENCODED_DOCUMENT + 4096
DOCUMENTS = {'DNI_FRONTAL', 'DNI_REVERSO', 'SELFIE'}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.post('/evaluate')
async def evaluate(request: Request):
    token = os.getenv('IDENTITY_SERVICE_TOKEN', '')
    expected = f'Bearer {token}'.encode()
    provided = request.headers.get('authorization', '').encode('latin-1')
    if len(token) < 32 or not hmac.compare_digest(provided, expected):
        raise HTTPException(401, 'Unauthorized')

    body = bytearray()
    try:
        async with asyncio.timeout(15):
            async for chunk in request.stream():
                if len(body) + len(chunk) > MAX_BODY:
                    raise HTTPException(413, 'Payload too large')
                body.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, 'Request timed out') from None

    try:
        payload = json.loads(body)
        if not isinstance(payload, dict) or set(payload) != {'dni', 'nombre_completo', 'documents'}:
            raise ValueError
        dni, name, documents = payload['dni'], payload['nombre_completo'], payload['documents']
        if not isinstance(dni, str) or len(dni) != 13 or not dni.isascii() or not dni.isdigit():
            raise ValueError
        if not isinstance(name, str) or not 0 < len(name) <= 150:
            raise ValueError
        if not isinstance(documents, dict) or set(documents) != DOCUMENTS:
            raise ValueError
        for image in documents.values():
            if not isinstance(image, str) or len(image) > MAX_ENCODED_DOCUMENT:
                raise ValueError
            if not 0 < len(base64.b64decode(image, validate=True)) <= MAX_DOCUMENT:
                raise ValueError
    except ValueError:
        raise HTTPException(400, 'Invalid identity request') from None

    try:
        return await run_in_threadpool(run_worker, payload)
    except Exception:
        raise HTTPException(503, 'Identity evaluation unavailable') from None
