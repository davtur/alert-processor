"""Call xAI Grok for structured remediation recommendations."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app import config

log = logging.getLogger("alert-processor.grok")

SYSTEM_PROMPT = """You are an OpenShift 4 cluster SRE assistant for a personal GitOps cluster (delta.drtsoft.com).
All permanent cluster configuration must go through Git and a pull request on github.com/davtur/openshift-delta.
You recommend a single next action for the incoming Alertmanager group.

Reply with JSON only (no markdown) using this schema:
{
  "summary": "one sentence",
  "root_cause": "likely cause",
  "risk": "low|medium|high",
  "action_type": "restart_deployment|delete_pod|scale_deployment|gitops_pr|acknowledge",
  "target": {"namespace": "", "kind": "Deployment|Pod", "name": "", "replicas": null},
  "gitops": {"path": "", "yaml_or_patch": "", "rationale": ""}
}

Rules:
- action_type must be one of the five values above.
- restart_deployment: CrashLoopBackOff / stuck rollout where a restart is likely enough. kind must be Deployment.
- delete_pod: a single named pod is stuck (Terminating, node gone). kind must be Pod, name is the pod name.
- scale_deployment: replica count is clearly wrong. replicas must be an integer 1-10.
- gitops_pr: config, RBAC, operators, routes, PVCs, monitoring, or anything that should persist. path must be relative under apps-kustomize/, cluster-kustomize/, operator-subscriptions/, apps-argo/, or gitops-oai/. yaml_or_patch is the full file content or a unified diff.
- acknowledge: informational, no safe action, or insufficient data. Prefer this over guessing.
- Never recommend freeform shell, oc apply, or deleting namespaces.
- Never invent resource names that are not in the alert labels/annotations or cluster context.
- If namespace is openshift-* or kube-*, prefer acknowledge unless the alert clearly names a restartable workload in that same namespace.
"""


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model response")
    return json.loads(text[start : end + 1])


def _normalize(rec: dict[str, Any]) -> dict[str, Any]:
    action = str(rec.get("action_type") or "acknowledge").strip()
    if action not in config.ALLOWED_ACTION_TYPES:
        action = "acknowledge"
    target = rec.get("target") if isinstance(rec.get("target"), dict) else {}
    gitops = rec.get("gitops") if isinstance(rec.get("gitops"), dict) else {}
    replicas = target.get("replicas")
    try:
        replicas_n = int(replicas) if replicas is not None and replicas != "" else None
    except (TypeError, ValueError):
        replicas_n = None
    return {
        "summary": str(rec.get("summary") or "No summary provided."),
        "root_cause": str(rec.get("root_cause") or ""),
        "risk": str(rec.get("risk") or "medium"),
        "action_type": action,
        "target": {
            "namespace": str(target.get("namespace") or ""),
            "kind": str(target.get("kind") or ""),
            "name": str(target.get("name") or ""),
            "replicas": replicas_n,
        },
        "gitops": {
            "path": str(gitops.get("path") or ""),
            "yaml_or_patch": str(gitops.get("yaml_or_patch") or ""),
            "rationale": str(gitops.get("rationale") or ""),
        },
    }


def recommend(payload: dict[str, Any], cluster_context: str = "") -> dict[str, Any]:
    if not config.XAI_API_KEY:
        log.warning("XAI_API_KEY is not set; returning acknowledge")
        return _normalize(
            {
                "summary": "Grok API key is not configured.",
                "root_cause": "Missing XAI_API_KEY",
                "risk": "low",
                "action_type": "acknowledge",
            }
        )

    user = {
        "alertmanager_payload": payload,
        "cluster_context": cluster_context,
    }
    body = {
        "model": config.XAI_MODEL,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(user, indent=2),
            },
        ],
    }
    try:
        with httpx.Client(timeout=90.0) as client:
            response = client.post(
                config.XAI_API_URL,
                headers={
                    "Authorization": f"Bearer {config.XAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return _normalize(_extract_json(content))
    except Exception as exc:
        log.exception("Grok recommendation failed")
        return _normalize(
            {
                "summary": f"Grok call failed: {exc}",
                "root_cause": "LLM error",
                "risk": "low",
                "action_type": "acknowledge",
            }
        )
