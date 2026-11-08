"""Return safe HTTP errors so database failures are not masked as browser CORS errors."""
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger('uvicorn.error')


async def database_error_response(request: Request, error: SQLAlchemyError):
    original = getattr(error, 'orig', None)
    code = getattr(original, 'sqlstate', None)
    missing_schema = code in ('42P01', '42703') or (
        type(original).__module__ == 'sqlite3' and 'no such table' in str(original)
    )
    logger.error('Database request failed: %s (sqlstate=%s)', type(error).__name__, code or 'unknown')
    detail = ('La base de datos necesita actualizarse antes de usar la revisión documental.'
              if missing_schema else
              'No pudimos consultar la base de datos. Intenta nuevamente en un momento.')
    return JSONResponse(status_code=503, content={'detail': detail}, headers={'Cache-Control':'no-store'})
