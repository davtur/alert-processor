"""Read-only cluster investigation tools for Grok. No mutations."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from app import config, k8s

log = logging.getLogger("alert-processor.investigate")

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_workloads",
            "description": "List Deployments, DaemonSets, StatefulSets, and Pods in a namespace with replica and crash status.",
            "parameters": {
                "type": "object",
                "properties": {"namespace": {"type": "string"}},
                "required": ["namespace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_pod",
            "description": "Get one pod's phase, node, owners, restart counts, and waiting/terminated reasons.",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "name": {"type": "string"},
                },
                "required": ["namespace", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_logs",
            "description": "Read the last lines of a pod container log. Use previous=true for the last crashed instance.",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "name": {"type": "string", "description": "Pod name"},
                    "container": {"type": "string"},
                    "previous": {"type": "boolean"},
                },
                "required": ["namespace", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_events",
            "description": "List recent Kubernetes events in a namespace. Optionally filter by object name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "name": {"type": "string", "description": "Optional involved object name"},
                },
                "required": ["namespace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_workload",
            "description": "Get a Deployment, DaemonSet, or StatefulSet including images and conditions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "kind": {"type": "string", "enum": ["Deployment", "DaemonSet", "StatefulSet"]},
                    "name": {"type": "string"},
                },
                "required": ["namespace", "kind", "name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_nodes",
            "description": "List cluster nodes with Ready status, roles, and GPU capacity if present.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

_HANDLERS: dict[str, Callable[..., Any]] = {
    "list_workloads": lambda **a: k8s.inspect_workloads(a["namespace"]),
    "get_pod": lambda **a: k8s.inspect_pod(a["namespace"], a["name"]),
    "get_logs": lambda **a: k8s.inspect_logs(
        a["namespace"],
        a["name"],
        a.get("container") or "",
        bool(a.get("previous")),
    ),
    "list_events": lambda **a: k8s.inspect_events(a["namespace"], a.get("name") or ""),
    "get_workload": lambda **a: k8s.inspect_workload(a["namespace"], a["kind"], a["name"]),
    "list_nodes": lambda **a: k8s.inspect_nodes(),
}


def run_tool(name: str, arguments: dict[str, Any]) -> str:
    handler = _HANDLERS.get(name)
    if handler is None:
        return json.dumps({"error": f"unknown tool {name}"})
    try:
        result = handler(**arguments)
    except Exception as exc:
        log.warning("tool %s failed: %s", name, exc)
        return json.dumps({"error": str(exc)})
    blob = json.dumps(result, default=str)
    if len(blob) > config.TOOL_RESULT_MAX_CHARS:
        blob = blob[: config.TOOL_RESULT_MAX_CHARS] + '..."}'
    return blob


def tool_calls_from_message(message: dict[str, Any]) -> list[dict[str, Any]]:
    calls = message.get("tool_calls") or []
    if calls:
        return calls
    # Some models nest under function_call
    single = message.get("function_call")
    if single:
        return [{"id": "call_0", "function": single}]
    return []
