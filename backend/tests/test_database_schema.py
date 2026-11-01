import unittest
from unittest.mock import Mock, patch

from app.database import database_schema, apply_database_schema


class DatabaseSchemaTests(unittest.TestCase):
    def test_only_public_or_valid_development_names_are_allowed(self):
        self.assertEqual(database_schema('public'), 'public')
        self.assertEqual(database_schema('teknigo_dev'), 'teknigo_dev')
        self.assertEqual(database_schema('teknigo_dev_test'), 'teknigo_dev_test')
        for value in ('', 'auth', 'storage', 'pg_catalog', 'public, teknigo_dev',
                      'teknigo_dev; DROP SCHEMA public', 'teknigo_dev_' + 'x' * 60):
            with self.assertRaises(RuntimeError):
                database_schema(value)

    def test_production_default_does_not_change_connection_settings(self):
        connection = Mock()
        apply_database_schema(connection, 'public')
        connection.exec_driver_sql.assert_not_called()

    def test_dev_search_path_excludes_public_and_confirms_actual_schema(self):
        connection = Mock()
        connection.exec_driver_sql.return_value.scalar_one.return_value = 'teknigo_dev'
        apply_database_schema(connection, 'teknigo_dev')
        statements = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
        self.assertEqual(statements[0], 'SET LOCAL search_path TO "teknigo_dev", pg_catalog')
        self.assertNotIn('public', statements[0])

    def test_missing_development_schema_fails_instead_of_using_production(self):
        connection = Mock()
        connection.exec_driver_sql.return_value.scalar_one.return_value = 'pg_catalog'
        with self.assertRaises(RuntimeError):
            apply_database_schema(connection, 'teknigo_dev')

    def test_environment_selects_dev_without_changing_database_url(self):
        with patch.dict('os.environ', {'DATABASE_SCHEMA': 'teknigo_dev'}):
            self.assertEqual(database_schema(), 'teknigo_dev')


if __name__ == '__main__':
    unittest.main()
