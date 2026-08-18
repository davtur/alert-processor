"""Runtime configuration from environment."""

from __future__ import annotations

import os
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


XAI_API_KEY = _env("XAI_API_KEY")
XAI_MODEL = _env("XAI_MODEL", "grok-4-1-fast-non-reasoning")
XAI_API_URL = _env("XAI_API_URL", "https://api.x.ai/v1/chat/completions")

SMTP_HOST = _env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(_env("SMTP_PORT", "587") or "587")
SMTP_USER = _env("SMTP_USER", "davtur@gmail.com")
SMTP_PASSWORD = _env("SMTP_PASSWORD")
MAIL_TO = _env("MAIL_TO", "davtur@gmail.com")
MAIL_FROM = _env("MAIL_FROM", SMTP_USER or "davtur@gmail.com")

GITHUB_TOKEN = _env("GITHUB_TOKEN")
GITHUB_REPO = _env("GITHUB_REPO", "davtur/openshift-delta")
GITHUB_API = _env("GITHUB_API", "https://api.github.com")

AUTH_PASSWORD = _env("AUTH_PASSWORD")
SIGNING_SECRET = _env("SIGNING_SECRET") or AUTH_PASSWORD or "dev-signing-secret"

PUBLIC_BASE_URL = _env("PUBLIC_BASE_URL", "https://alert-processor.apps.delta.drtsoft.com").rstrip("/")
OAUTH_COOKIE_NAME = _env("OAUTH_COOKIE_NAME", "_oauth_proxy_ap")
OAUTH_LOGOUT_URL = _env(
    "OAUTH_LOGOUT_URL",
    "https://oauth-openshift.apps.delta.drtsoft.com/logout?then=https://alert-processor.apps.delta.drtsoft.com",
)
DATA_DIR = Path(_env("DATA_DIR", "/data"))
DB_PATH = Path(_env("DB_PATH", str(DATA_DIR / "alert-processor.db")))

SKIP_ALERTNAMES = {
    name.strip()
    for name in _env("SKIP_ALERTNAMES", "Watchdog,InfoInhibitor").split(",")
    if name.strip()
}

MAX_SCALE_REPLICAS = int(_env("MAX_SCALE_REPLICAS", "10") or "10")
MAX_INVESTIGATE_ROUNDS = int(_env("MAX_INVESTIGATE_ROUNDS", "8") or "8")
LOG_TAIL_LINES = int(_env("LOG_TAIL_LINES", "80") or "80")
TOOL_RESULT_MAX_CHARS = int(_env("TOOL_RESULT_MAX_CHARS", "8000") or "8000")
TOKEN_TTL_SECONDS = int(_env("TOKEN_TTL_SECONDS", str(24 * 60 * 60)) or str(24 * 60 * 60))
NOTIFY_COOLDOWN_SECONDS = int(_env("NOTIFY_COOLDOWN_SECONDS", str(3 * 60 * 60)) or str(3 * 60 * 60))
SESSION_TTL_SECONDS = int(_env("SESSION_TTL_SECONDS", str(30 * 24 * 60 * 60)) or str(30 * 24 * 60 * 60))

ALLOWED_ACTION_TYPES = frozenset(
    {
        "restart_deployment",
        "delete_pod",
        "scale_deployment",
        "gitops_pr",
        "acknowledge",
    }
)

GITOPS_PATH_PREFIXES = (
    "apps-kustomize/",
    "cluster-kustomize/",
    "operator-subscriptions/",
    "apps-argo/",
    "gitops-oai/",
)

ALWAYS_DENY_NAMESPACES = frozenset({"kube-system", "kube-public", "kube-node-lease"})
