import os
import tempfile
import unittest

os.environ["SIGNING_SECRET"] = "unit-test-secret"
os.environ["AUTH_PASSWORD"] = "unit-test-password"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="alert-processor-")
os.environ["XAI_API_KEY"] = ""
os.environ["SMTP_PASSWORD"] = ""
os.environ["GITHUB_TOKEN"] = ""
os.environ["PUBLIC_BASE_URL"] = "http://127.0.0.1:8080"

from fastapi.testclient import TestClient

from app.main import app


class WebhookTests(unittest.TestCase):
    def test_watchdog_skipped(self):
        client = TestClient(app)
        res = client.post(
            "/api/v1/webhook",
            json={
                "status": "firing",
                "groupKey": "Watchdog",
                "commonLabels": {"alertname": "Watchdog"},
                "alerts": [{"labels": {"alertname": "Watchdog"}, "status": "firing"}],
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "skipped")

    def test_healthz(self):
        client = TestClient(app)
        self.assertEqual(client.get("/healthz").json(), {"status": "ok"})
