import os
import unittest
from unittest.mock import patch

from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware

from app.cors import LOCAL_ORIGINS, allowed_origins


class CorsTests(unittest.TestCase):
    def preflight(self, origin):
        middleware = CORSMiddleware(
            app=None, allow_origins=allowed_origins(),
            allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"],
        )
        return middleware.preflight_response(Headers({
            "origin": origin, "access-control-request-method": "POST",
            "access-control-request-headers": "authorization,content-type",
        }))

    @patch.dict(os.environ, {"CORS_ORIGINS": LOCAL_ORIGINS})
    def test_both_local_hosts_accept_authenticated_json_preflight(self):
        for origin in LOCAL_ORIGINS.split(","):
            with self.subTest(origin=origin):
                response = self.preflight(origin)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["access-control-allow-origin"], origin)

    @patch.dict(os.environ, {"CORS_ORIGINS": " https://teknigo.example/ , https://teknigo.example "})
    def test_configuration_is_trimmed_and_deduplicated(self):
        self.assertEqual(allowed_origins(), ["https://teknigo.example"])
        self.assertEqual(self.preflight("https://teknigo.example").status_code, 200)

    @patch.dict(os.environ, {"CORS_ORIGINS": "https://teknigo.example"})
    def test_unlisted_origins_remain_rejected(self):
        self.assertEqual(self.preflight("https://otro.example").status_code, 400)
        self.assertEqual(self.preflight("http://localhost:5173").status_code, 400)
