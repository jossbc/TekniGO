import subprocess
import unittest
from unittest.mock import patch

from app.check_database import CHECK_TIMEOUT_SECONDS, main


class CheckTimeoutTests(unittest.TestCase):
    @patch("app.check_database.subprocess.run")
    def test_hung_driver_is_stopped_and_reports_failure(self, run):
        run.side_effect = subprocess.TimeoutExpired("check", CHECK_TIMEOUT_SECONDS)
        with patch("builtins.print") as output:
            self.assertEqual(main(), 1)
        self.assertEqual(run.call_args.kwargs["timeout"], 25)
        self.assertTrue(any("Tiempo agotado" in str(call) for call in output.call_args_list))

    @patch("app.check_database.subprocess.run")
    def test_worker_result_is_not_reported_as_success_when_failed(self, run):
        run.return_value.returncode = 1
        with patch("builtins.print"):
            self.assertEqual(main(), 1)
