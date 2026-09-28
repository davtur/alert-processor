import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SIGNING_SECRET", "unit-test-secret")
os.environ.setdefault("AUTH_PASSWORD", "unit-test-password")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="alert-processor-models-"))
os.environ.setdefault("XAI_API_KEY", "")
os.environ.setdefault("GITHUB_TOKEN", "")

from fastapi.testclient import TestClient

from app import catalog, config, db, grok
from app.main import app

db.init()


def _write_catalog(text: str) -> str:
    handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    handle.write(text)
    handle.close()
    return handle.name


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self._file = config.MODELS_FILE
        self._selected = db.get_setting("selected_model")

    def tearDown(self):
        config.MODELS_FILE = self._file
        if self._selected is None:
            db.set_setting("selected_model", "")
        else:
            db.set_setting("selected_model", self._selected)

    def test_env_fallback_when_file_unset(self):
        config.MODELS_FILE = ""
        models = catalog.list_models()
        self.assertEqual([model.id for model in models], ["grok"])
        self.assertEqual(models[0].api_key_env, "XAI_API_KEY")

    def test_file_selection_and_default(self):
        config.MODELS_FILE = _write_catalog(
            """
models:
  - id: grok
    label: Grok
    model: grok-test
    apiUrl: https://api.example.com/v1/chat/completions
    apiKeyEnv: XAI_API_KEY
    default: true
  - id: local
    label: Local model
    model: local-model
    apiUrl: http://model.models.svc/v1/chat/completions
    toolResultMaxChars: 1500
"""
        )
        self.assertEqual(catalog.selected().id, "grok")
        catalog.select("local")
        chosen = catalog.selected()
        self.assertEqual(chosen.id, "local")
        self.assertEqual(chosen.tool_result_max_chars, 1500)
        self.assertEqual(catalog.public_state()["selected"], "local")
        with self.assertRaises(catalog.CatalogError):
            catalog.select("missing")

    def test_rejects_bad_url(self):
        config.MODELS_FILE = _write_catalog(
            """
models:
  - id: bad
    label: Bad
    model: x
    apiUrl: ftp://example.com
"""
        )
        with self.assertRaises(catalog.CatalogError):
            catalog.list_models()

    def test_tool_limit_is_scoped(self):
        token = catalog.push_tool_limit(1500)
        try:
            self.assertEqual(catalog.tool_result_max_chars(), 1500)
        finally:
            catalog.pop_tool_limit(token)
        self.assertEqual(catalog.tool_result_max_chars(), config.TOOL_RESULT_MAX_CHARS)

    def test_message_text_strips_think_blocks(self):
        text = grok._message_text(
            {"content": "<think>secret</think>\n{\"action_type\": \"acknowledge\"}"}
        )
        self.assertNotIn("secret", text)
        self.assertIn("acknowledge", text)
        self.assertEqual(
            grok._message_text({"content": "", "reasoning_content": "{\"ok\": true}"}),
            '{"ok": true}',
        )


class ModelApiTests(unittest.TestCase):
    def setUp(self):
        self._file = config.MODELS_FILE
        self._selected = db.get_setting("selected_model")
        self._catalog = _write_catalog(
            """
models:
  - id: grok
    label: Grok
    model: grok-test
    apiUrl: https://api.example.com/v1/chat/completions
    default: true
  - id: local
    label: Local model
    model: local-model
    apiUrl: http://model.models.svc/v1/chat/completions
"""
        )
        config.MODELS_FILE = self._catalog

    def tearDown(self):
        config.MODELS_FILE = self._file
        db.set_setting("selected_model", self._selected or "")
        Path(self._catalog).unlink(missing_ok=True)

    def test_models_require_login_and_switch(self):
        client = TestClient(app)
        self.assertEqual(client.get("/api/v1/models").status_code, 401)
        headers = {"X-Forwarded-User": "dev"}
        listed = client.get("/api/v1/models", headers=headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["selected"], "grok")
        self.assertEqual([item["id"] for item in listed.json()["models"]], ["grok", "local"])
        switched = client.post("/api/v1/models", headers=headers, json={"id": "local"})
        self.assertEqual(switched.status_code, 200)
        self.assertEqual(switched.json()["selected"], "local")
        rejected = client.post("/api/v1/models", headers=headers, json={"id": "other"})
        self.assertEqual(rejected.status_code, 400)


if __name__ == "__main__":
    unittest.main()
