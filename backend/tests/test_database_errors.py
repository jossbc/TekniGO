import json
import sqlite3
from types import SimpleNamespace
import unittest

from fastapi import FastAPI
from sqlalchemy.exc import OperationalError, ProgrammingError, SQLAlchemyError

from app.cors import configure_cors
from app.database_errors import database_error_response


class DatabaseErrorTests(unittest.TestCase):
    def respond(self, error):
        task = database_error_response(None, error)
        try: task.send(None)
        except StopIteration as result: return result.value
        self.fail('The error handler unexpectedly suspended')

    def test_missing_migration_is_reported_without_sql_or_credentials(self):
        for original in [SimpleNamespace(sqlstate='42P01'), SimpleNamespace(sqlstate='42703'), sqlite3.OperationalError('no such table: decisiones_documentales')]:
            response = self.respond(ProgrammingError('secret sql', {'password':'private'}, original))
            self.assertEqual(response.status_code, 503)
            self.assertIn('actualizarse', json.loads(response.body)['detail'])
            self.assertNotIn('secret', response.body.decode())
            self.assertNotIn('private', response.body.decode())

    def test_database_connection_errors_do_not_expose_provider_messages(self):
        response = self.respond(OperationalError('sql', {}, Exception('private connection string')))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('private', response.body.decode())

    def test_database_failure_keeps_cors_headers_for_the_frontend(self):
        app = FastAPI()
        configure_cors(app)
        app.add_exception_handler(SQLAlchemyError, database_error_response)
        @app.get('/dossier')
        async def broken_dossier():
            raise ProgrammingError('private sql', {}, SimpleNamespace(sqlstate='42P01'))
        messages = []
        async def receive(): return {'type':'http.request','body':b'','more_body':False}
        async def send(message): messages.append(message)
        task = app({'type':'http','asgi':{'version':'3.0'},'method':'GET','path':'/dossier',
            'raw_path':b'/dossier','query_string':b'','scheme':'http','root_path':'',
            'headers':[(b'origin',b'http://localhost:5173')], 'server':('localhost',8000), 'client':('localhost',1)}, receive, send)
        try: task.send(None)
        except StopIteration: pass
        else: self.fail('The in-memory ASGI request unexpectedly suspended')
        start = next(message for message in messages if message['type']=='http.response.start')
        self.assertEqual(start['status'], 503)
        self.assertEqual(dict(start['headers'])[b'access-control-allow-origin'], b'http://localhost:5173')


if __name__ == '__main__': unittest.main()
