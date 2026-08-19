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

from app import db
from app.main import app

db.init()


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

    def test_session_unauthenticated(self):
        client = TestClient(app)
        self.assertEqual(client.get("/api/v1/session").json()["authenticated"], False)

    def test_openshift_header_authenticates(self):
        client = TestClient(app)
        res = client.get("/api/v1/session", headers={"X-Forwarded-User": "davtur"})
        data = res.json()
        self.assertTrue(data["authenticated"])
        self.assertEqual(data["user"], "davtur")
        self.assertEqual(data["auth"], "openshift")
        inbox = client.get("/api/v1/incidents", headers={"X-Forwarded-User": "davtur"})
        self.assertEqual(inbox.status_code, 200)

    def test_openshift_email_header_authenticates(self):
        client = TestClient(app)
        res = client.get(
            "/api/v1/session",
            headers={"X-Forwarded-User": "", "X-Forwarded-Email": "david@manlyit.com.au"},
        )
        data = res.json()
        self.assertTrue(data["authenticated"])
        self.assertEqual(data["user"], "david@manlyit.com.au")
        self.assertEqual(data["auth"], "openshift")

    def test_incidents_require_login(self):
        client = TestClient(app)
        self.assertEqual(client.get("/api/v1/incidents").status_code, 401)
