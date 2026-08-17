from __future__ import annotations

ALLOWED_ACTIONS = (
    "restart_deployment",
    "delete_pod",
    "scale_deployment",
    "gitops_pr",
    "acknowledge",
)

RESOURCE_NAME_RE = r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"
