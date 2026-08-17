"""Whitelisted Kubernetes runtime actions via the in-cluster API."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from app import config

log = logging.getLogger("alert-processor.k8s")

NAME_RE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


class ActionError(ValueError):
    pass


def _client():
    from kubernetes import client, config as k8s_config

    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
    return client


def valid_name(value: str) -> bool:
    return bool(value) and bool(NAME_RE.fullmatch(value)) and len(value) <= 253


def namespace_allowed(namespace: str, origin_namespace: str) -> bool:
    if not valid_name(namespace):
        return False
    if namespace in config.ALWAYS_DENY_NAMESPACES:
        return False
    if namespace.startswith("kube-") or namespace.startswith("openshift-"):
        return namespace == origin_namespace
    return True


def _require_target(rec: dict[str, Any], origin_namespace: str) -> tuple[str, str]:
    target = rec.get("target") or {}
    namespace = str(target.get("namespace") or origin_namespace or "")
    name = str(target.get("name") or "")
    if not valid_name(namespace) or not valid_name(name):
        raise ActionError("target namespace/name failed validation")
    if not namespace_allowed(namespace, origin_namespace):
        raise ActionError(f"writes to namespace {namespace} are not allowed")
    return namespace, name


def cluster_context(namespace: str) -> str:
    if not namespace or not valid_name(namespace):
        return ""
    try:
        client = _client()
        apps = client.AppsV1Api()
        core = client.CoreV1Api()
        deployments = apps.list_namespaced_deployment(namespace, limit=20)
        pods = core.list_namespaced_pod(namespace, limit=30)
        dep_lines = [
            f"Deployment {d.metadata.name} replicas={d.spec.replicas} ready={d.status.ready_replicas}"
            for d in deployments.items
        ]
        pod_lines = []
        for p in pods.items:
            phase = p.status.phase
            waiting = ""
            if p.status.container_statuses:
                state = p.status.container_statuses[0].state
                if state.waiting:
                    waiting = state.waiting.reason or ""
            pod_lines.append(f"Pod {p.metadata.name} phase={phase} waiting={waiting}")
        return "\n".join(dep_lines + pod_lines)
    except Exception:
        log.exception("Failed to collect cluster context for %s", namespace)
        return ""


def restart_deployment(namespace: str, name: str) -> str:
    client = _client()
    apps = client.AppsV1Api()
    now = datetime.now(timezone.utc).isoformat()
    body = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {"kubectl.kubernetes.io/restartedAt": now}
                }
            }
        }
    }
    apps.patch_namespaced_deployment(name, namespace, body)
    return f"Restarted Deployment {namespace}/{name}"


def delete_pod(namespace: str, name: str) -> str:
    client = _client()
    core = client.CoreV1Api()
    core.delete_namespaced_pod(name, namespace)
    return f"Deleted Pod {namespace}/{name}"


def scale_deployment(namespace: str, name: str, replicas: int) -> str:
    if replicas < 1 or replicas > config.MAX_SCALE_REPLICAS:
        raise ActionError(f"replicas must be 1-{config.MAX_SCALE_REPLICAS}")
    client = _client()
    apps = client.AppsV1Api()
    body = {"spec": {"replicas": replicas}}
    apps.patch_namespaced_deployment_scale(name, namespace, body)
    return f"Scaled Deployment {namespace}/{name} to {replicas}"


def execute(rec: dict[str, Any], origin_namespace: str) -> str:
    action = rec.get("action_type")
    if action == "acknowledge":
        return "Acknowledged; no cluster change."
    if action == "gitops_pr":
        raise ActionError("gitops_pr is handled by the GitHub client")
    namespace, name = _require_target(rec, origin_namespace)
    if action == "restart_deployment":
        return restart_deployment(namespace, name)
    if action == "delete_pod":
        return delete_pod(namespace, name)
    if action == "scale_deployment":
        replicas = (rec.get("target") or {}).get("replicas")
        try:
            replicas_n = int(replicas)
        except (TypeError, ValueError) as exc:
            raise ActionError("scale_deployment requires integer replicas") from exc
        return scale_deployment(namespace, name, replicas_n)
    raise ActionError(f"unsupported action_type {action}")
