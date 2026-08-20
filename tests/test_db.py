import os
import tempfile
import unittest

os.environ.setdefault("SIGNING_SECRET", "unit-test-secret")
os.environ.setdefault("AUTH_PASSWORD", "unit-test-password")
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="alert-processor-db-")
os.environ["DATABASE_URL"] = ""
os.environ.setdefault("XAI_API_KEY", "")
os.environ.setdefault("SMTP_PASSWORD", "")
os.environ.setdefault("GITHUB_TOKEN", "")

from app import db


class SqliteDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init()

    def test_upsert_and_list(self):
        item = db.upsert_incident(
            fingerprint="fp-db-test",
            group_key="{}:{alertname=Test}",
            status="firing",
            alertname="TestAlert",
            namespace="demo",
            severity="warning",
            payload={"alerts": [{"labels": {"alertname": "TestAlert"}}]},
        )
        self.assertTrue(item["id"])
        self.assertEqual(item["fingerprint"], "fp-db-test")
        self.assertEqual(item["payload"]["alerts"][0]["labels"]["alertname"], "TestAlert")

        again = db.upsert_incident(
            fingerprint="fp-db-test",
            group_key="{}:{alertname=Test}",
            status="resolved",
            alertname="TestAlert",
            namespace="demo",
            severity="warning",
            payload={"status": "resolved"},
        )
        self.assertEqual(again["id"], item["id"])
        self.assertEqual(again["status"], "resolved")

        listed = db.list_incidents()
        self.assertTrue(any(row["fingerprint"] == "fp-db-test" for row in listed))

    def test_recommendation_and_audit(self):
        item = db.upsert_incident(
            fingerprint="fp-db-rec",
            group_key="g",
            status="firing",
            alertname="Other",
            namespace="demo",
            severity="critical",
            payload={},
        )
        db.save_recommendation(item["id"], {"action_type": "acknowledge", "summary": "ok"})
        db.add_audit(item["id"], "analyze", "grok", "done")
        loaded = db.get_by_id(item["id"])
        self.assertEqual(loaded["recommendation"]["action_type"], "acknowledge")
        self.assertTrue(db.ping())
        self.assertIn("alert-processor.db", db.describe())


if __name__ == "__main__":
    unittest.main()
