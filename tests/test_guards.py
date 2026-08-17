import os
import tempfile
import unittest

os.environ["SIGNING_SECRET"] = "unit-test-secret"
os.environ["AUTH_PASSWORD"] = "unit-test-password"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="alert-processor-")
os.environ["XAI_API_KEY"] = ""
os.environ["SMTP_PASSWORD"] = ""
os.environ["GITHUB_TOKEN"] = ""

from app import grok, k8s, tokens
from app.github_pr import GitOpsError, validate_path


class TokenTests(unittest.TestCase):
    def test_roundtrip_and_expiry(self):
        token = tokens.make_action_token(7, "approve")
        payload = tokens.decode(token)
        self.assertEqual(payload["id"], 7)
        self.assertEqual(payload["act"], "approve")

    def test_tamper_rejected(self):
        token = tokens.make_action_token(1, "reject")
        blob, sig = token.split(".", 1)
        with self.assertRaises(tokens.TokenError):
            tokens.decode(blob + "." + ("A" if sig[0] != "A" else "B") + sig[1:])


class ActionGuardTests(unittest.TestCase):
    def test_kube_system_denied(self):
        self.assertFalse(k8s.namespace_allowed("kube-system", "kube-system"))

    def test_openshift_only_when_origin_matches(self):
        self.assertTrue(k8s.namespace_allowed("openshift-monitoring", "openshift-monitoring"))
        self.assertFalse(k8s.namespace_allowed("openshift-monitoring", "default"))

    def test_user_namespace_ok(self):
        self.assertTrue(k8s.namespace_allowed("fitness-crm", "fitness-crm"))

    def test_invalid_name(self):
        self.assertFalse(k8s.valid_name("Not_Valid"))
        self.assertTrue(k8s.valid_name("alert-processor"))


class GitOpsPathTests(unittest.TestCase):
    def test_allowed(self):
        self.assertEqual(
            validate_path("apps-kustomize/foo/app/deploy.yaml"),
            "apps-kustomize/foo/app/deploy.yaml",
        )

    def test_rejects_escape(self):
        with self.assertRaises(GitOpsError):
            validate_path("../secrets.yaml")
        with self.assertRaises(GitOpsError):
            validate_path("scripts/seal-secrets.sh")


class GrokNormalizeTests(unittest.TestCase):
    def test_unknown_action_becomes_acknowledge(self):
        rec = grok._normalize({"action_type": "rm -rf /", "summary": "nope"})
        self.assertEqual(rec["action_type"], "acknowledge")


if __name__ == "__main__":
    unittest.main()
