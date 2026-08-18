import json
import os
import tempfile
import unittest

os.environ["SIGNING_SECRET"] = "unit-test-secret"
os.environ["AUTH_PASSWORD"] = "unit-test-password"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="alert-processor-")
os.environ["XAI_API_KEY"] = ""
os.environ["SMTP_PASSWORD"] = ""
os.environ["GITHUB_TOKEN"] = ""

from app import grok, k8s, priority, tokens
from app.github_pr import GitOpsError, has_yaml_proposal, resolve_path, validate_path, yaml_body


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

    def test_yaml_body_strips_fences(self):
        body = yaml_body("```yaml\napiVersion: v1\nkind: ConfigMap\n```")
        self.assertIn("kind: ConfigMap", body)
        self.assertFalse(body.startswith("```"))

    def test_has_yaml_proposal(self):
        self.assertFalse(has_yaml_proposal({"gitops": {"yaml_or_patch": ""}}))
        self.assertFalse(has_yaml_proposal({"gitops": {"yaml_or_patch": "# just a comment"}}))
        self.assertTrue(
            has_yaml_proposal({"gitops": {"yaml_or_patch": "apiVersion: v1\nkind: ConfigMap\n"}})
        )

    def test_resolve_path_fallback(self):
        path = resolve_path(
            {"id": 42, "alertname": "KubePodCrashLooping"},
            {"gitops": {"path": "not-a-valid-path", "yaml_or_patch": "kind: ConfigMap\n"}},
        )
        self.assertEqual(path, "apps-kustomize/alert-processor/proposals/42-kubepodcrashlooping.yaml")


class PriorityTests(unittest.TestCase):
    def test_critical_before_warning(self):
        items = [
            {
                "id": 1,
                "severity": "warning",
                "status": "firing",
                "updated_at": "2026-08-18T12:00:00+00:00",
                "recommendation": {"risk": "high"},
            },
            {
                "id": 2,
                "severity": "critical",
                "status": "firing",
                "updated_at": "2026-08-18T11:00:00+00:00",
                "recommendation": {"risk": "low"},
            },
            {
                "id": 3,
                "severity": "info",
                "status": "firing",
                "updated_at": "2026-08-18T13:00:00+00:00",
                "recommendation": {"risk": "medium"},
            },
        ]
        ordered = priority.sort_incidents(items)
        self.assertEqual([i["id"] for i in ordered], [2, 1, 3])

    def test_same_severity_uses_risk_then_recency(self):
        items = [
            {
                "id": 1,
                "severity": "warning",
                "status": "firing",
                "updated_at": "2026-08-18T10:00:00+00:00",
                "recommendation": {"risk": "low"},
            },
            {
                "id": 2,
                "severity": "warning",
                "status": "firing",
                "updated_at": "2026-08-18T12:00:00+00:00",
                "recommendation": {"risk": "high"},
            },
            {
                "id": 3,
                "severity": "warning",
                "status": "firing",
                "updated_at": "2026-08-18T11:00:00+00:00",
                "recommendation": {"risk": "high"},
            },
        ]
        ordered = priority.sort_incidents(items)
        self.assertEqual([i["id"] for i in ordered], [2, 3, 1])


class GrokNormalizeTests(unittest.TestCase):
    def test_unknown_action_becomes_acknowledge(self):
        rec = grok._normalize({"action_type": "rm -rf /", "summary": "nope"})
        self.assertEqual(rec["action_type"], "acknowledge")
        self.assertEqual(rec["how_to_resolve"], [])

    def test_how_to_resolve_list(self):
        rec = grok._normalize(
            {
                "action_type": "acknowledge",
                "how_to_resolve": ["check operator logs", "inspect DaemonSet"],
            }
        )
        self.assertEqual(rec["how_to_resolve"], ["check operator logs", "inspect DaemonSet"])
        self.assertIn("does not change the cluster", grok.approval_effect(rec))

    def test_approval_effect_includes_existing_pr(self):
        rec = grok._normalize(
            {
                "action_type": "acknowledge",
                "pr_url": "https://github.com/davtur/openshift-delta/pull/11",
            }
        )
        self.assertIn("https://github.com/davtur/openshift-delta/pull/11", grok.approval_effect(rec))


class InvestigateToolTests(unittest.TestCase):
    def test_unknown_tool(self):
        from app.investigate import run_tool

        out = json.loads(run_tool("rm", {}))
        self.assertIn("unknown tool", out["error"])

    def test_invalid_namespace(self):
        from app.investigate import run_tool

        out = json.loads(run_tool("list_workloads", {"namespace": "Not Valid"}))
        self.assertIn("error", out)


if __name__ == "__main__":
    unittest.main()
