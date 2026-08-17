"""Call xAI Grok for structured remediation recommendations."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from app import config

log = logging.getLogger("alert-processor.grok")

INVESTIGATE_PROMPT = """You are investigating a firing OpenShift 4 alert on the personal GitOps cluster delta.drtsoft.com.
You have READ-ONLY cluster tools. Use them to find the actual root cause before concluding.
Do not suggest executing mutations via tools — there are none. Do not invent resource names.
Typical sequence: list_workloads in the alert namespace, list_events, get_pod / get_logs for crashlooping containers, get_workload for the owner, list_nodes if this looks like GPU/node pressure.
When you have enough evidence, stop calling tools and write a concise findings report covering:
- what is broken
- evidence (pod names, log lines, events)
- likely root cause
- whether a restart would only mask it
- what a permanent GitOps fix would look like (file path under apps-kustomize/, cluster-kustomize/, operator-subscriptions/, apps-argo/, or gitops-oai/ if you can name one)
"""

SYSTEM_PROMPT = """You are an OpenShift 4 cluster SRE assistant for a personal GitOps cluster (delta.drtsoft.com).
You already ran a read-only investigation. Recommend a PERMANENT corrective action plus an optional short-term executable action.

All permanent cluster configuration must go through Git and a pull request on github.com/davtur/openshift-delta.

Reply with JSON only (no markdown) using this schema:
{
  "summary": "one sentence of what is broken",
  "root_cause": "root cause from the investigation evidence",
  "how_to_resolve": ["permanent step 1", "permanent step 2", "step 3"],
  "risk": "low|medium|high",
  "action_type": "restart_deployment|delete_pod|scale_deployment|gitops_pr|acknowledge",
  "target": {"namespace": "", "kind": "Deployment|Pod", "name": "", "replicas": null},
  "gitops": {"path": "", "yaml_or_patch": "", "rationale": ""}
}

Rules:
- Prefer gitops_pr when the lasting fix is config, operator settings, monitors, resources, node selectors, or anything that should survive a restart. Fill gitops.path and yaml_or_patch (full file or unified diff).
- Use restart_deployment / delete_pod / scale_deployment only as a stop-gap when that immediately restores service AND name a real resource from the investigation. Still put the permanent fix in how_to_resolve.
- acknowledge means there is no safe whitelist mutation to auto-run (operator internals, missing evidence, or the permanent fix cannot be expressed as a Git file yet). Still fill how_to_resolve with the permanent path.
- Always fill how_to_resolve with 3-6 concrete steps. Lead with the permanent fix, not a reboot.
- Never invent resource names that were not in the alert or investigation findings.
- Never recommend freeform shell, oc apply, or deleting namespaces as action_type.
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
        "investigation": str(rec.get("investigation") or ""),
        "investigation_status": str(rec.get("investigation_status") or "done"),
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


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.XAI_API_KEY}",
        "Content-Type": "application/json",
    }


def _chat(client: httpx.Client, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": config.XAI_MODEL,
        "temperature": 0.2,
        "messages": messages,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    response = client.post(config.XAI_API_URL, headers=_headers(), json=body)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]


def _investigate(client: httpx.Client, payload: dict[str, Any], cluster_context: str) -> str:
    from app.investigate import TOOLS, run_tool, tool_calls_from_message

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": INVESTIGATE_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {"alertmanager_payload": payload, "cluster_context": cluster_context},
                indent=2,
            ),
        },
    ]
    findings = ""
    for round_n in range(config.MAX_INVESTIGATE_ROUNDS):
        message = _chat(client, messages, TOOLS)
        calls = tool_calls_from_message(message)
        if not calls:
            findings = (message.get("content") or "").strip()
            log.info("Investigation finished after %s rounds (%s chars)", round_n + 1, len(findings))
            break
        messages.append(message)
        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = {}
            log.info("Investigate tool %s args=%s", name, args)
            result = run_tool(name, args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or f"call_{round_n}",
                    "content": result,
                }
            )
    else:
        findings = "Investigation hit the tool-round limit without a final write-up."
    return findings or "No investigation findings."


def recommend(payload: dict[str, Any], cluster_context: str = "") -> dict[str, Any]:
    if not config.XAI_API_KEY:
        log.warning("XAI_API_KEY is not set; returning acknowledge")
        return _normalize(
            {
                "summary": "Grok API key is not configured.",
                "root_cause": "Missing XAI_API_KEY",
                "risk": "low",
                "action_type": "acknowledge",
                "investigation_status": "done",
            }
        )

    try:
        with httpx.Client(timeout=60.0) as client:
            findings = _investigate(client, payload, cluster_context)
            conclude_messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "alertmanager_payload": payload,
                            "investigation_findings": findings,
                        },
                        indent=2,
                    ),
                },
            ]
            message = _chat(client, conclude_messages)
            content = message.get("content") or ""
            rec = _normalize(_extract_json(content))
            rec["investigation"] = findings
            rec["investigation_status"] = "done"
            log.info(
                "Grok recommendation action=%s risk=%s summary=%s how_to_resolve=%s rec=%s",
                rec.get("action_type"),
                rec.get("risk"),
                rec.get("summary"),
                rec.get("how_to_resolve"),
                json.dumps({k: v for k, v in rec.items() if k != "investigation"}, default=str),
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
                "investigation_status": "done",
            }
        )
