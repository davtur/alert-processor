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
You recommend a single next *executable* action AND always explain how a human should resolve the alert.

Reply with JSON only (no markdown) using this schema:
{
  "summary": "one sentence of what is broken",
  "root_cause": "likely cause",
  "how_to_resolve": ["step 1", "step 2", "step 3"],
  "risk": "low|medium|high",
  "action_type": "restart_deployment|delete_pod|scale_deployment|gitops_pr|acknowledge",
  "target": {"namespace": "", "kind": "Deployment|Pod", "name": "", "replicas": null},
  "gitops": {"path": "", "yaml_or_patch": "", "rationale": ""}
}

Rules:
- Always fill how_to_resolve with 3-6 concrete steps (oc commands, console checks, GitOps file paths). This is required even when action_type is acknowledge.
- action_type is the *only* mutation the app may auto-run after human approval. It is not the whole recommendation.
- restart_deployment: CrashLoopBackOff / stuck rollout where a restart is likely enough. kind must be Deployment.
- delete_pod: a single named pod is stuck (Terminating, node gone). kind must be Pod, name is the pod name.
- scale_deployment: replica count is clearly wrong. replicas must be an integer 1-10.
- gitops_pr: config, RBAC, operators, routes, PVCs, monitoring, or anything that should persist. path must be relative under apps-kustomize/, cluster-kustomize/, operator-subscriptions/, apps-argo/, or gitops-oai/. yaml_or_patch is the full file content or a unified diff.
- acknowledge means "do not auto-execute a cluster mutation" — it is a successful recommendation when the fix needs investigation, an operator, or a Git change you cannot safely write. It is NOT a failed Grok call.
- Never recommend freeform shell, oc apply, or deleting namespaces as the executable action_type.
- Never invent resource names that are not in the alert labels/annotations or cluster context.
- If namespace is openshift-* or kube-*, use acknowledge unless the alert clearly names a restartable workload in that same namespace — still provide how_to_resolve steps.
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
    how = rec.get("how_to_resolve")
    if isinstance(how, str) and how.strip():
        how_list = [how.strip()]
    elif isinstance(how, list):
        how_list = [str(step).strip() for step in how if str(step).strip()]
    else:
        how_list = []
    return {
        "summary": str(rec.get("summary") or "No summary provided."),
        "root_cause": str(rec.get("root_cause") or ""),
        "how_to_resolve": how_list,
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


def approval_effect(rec: dict[str, Any]) -> str:
    action = rec.get("action_type") or "acknowledge"
    target = rec.get("target") or {}
    ns = target.get("namespace") or ""
    name = target.get("name") or ""
    if action == "restart_deployment":
        return f"Approve will patch Deployment {ns}/{name} with a restart annotation. No Git change."
    if action == "delete_pod":
        return f"Approve will delete Pod {ns}/{name} so the controller can recreate it. No Git change."
    if action == "scale_deployment":
        return f"Approve will scale Deployment {ns}/{name} to {target.get('replicas')} replicas. No Git change."
    if action == "gitops_pr":
        path = (rec.get("gitops") or {}).get("path") or "a file in openshift-delta"
        return f"Approve will open a GitHub PR on davtur/openshift-delta for {path}. It will not merge or oc apply."
    return (
        "Approve with this action does not change the cluster. Use the how-to-resolve "
        "steps yourself, or Acknowledge only to dismiss."
    )


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
            rec = _normalize(_extract_json(content))
            log.info(
                "Grok recommendation action=%s risk=%s summary=%s how_to_resolve=%s rec=%s",
                rec.get("action_type"),
                rec.get("risk"),
                rec.get("summary"),
                rec.get("how_to_resolve"),
                json.dumps(rec, default=str),
            )
            return rec
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
