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


def _container_state(status) -> dict[str, Any]:
    if not status:
        return {}
    state = status.state
    info: dict[str, Any] = {
        "name": status.name,
        "ready": status.ready,
        "restarts": status.restart_count,
        "image": status.image,
    }
    if state.waiting:
        info["waiting"] = state.waiting.reason
        info["waiting_message"] = (state.waiting.message or "")[:300]
    if state.terminated:
        info["terminated"] = state.terminated.reason
        info["exit_code"] = state.terminated.exit_code
    if status.last_state and status.last_state.terminated:
        info["last_terminated"] = status.last_state.terminated.reason
        info["last_exit_code"] = status.last_state.terminated.exit_code
    return info


def inspect_workloads(namespace: str) -> dict[str, Any]:
    if not valid_name(namespace):
        raise ActionError("invalid namespace")
    client = _client()
    apps = client.AppsV1Api()
    core = client.CoreV1Api()
    deployments = apps.list_namespaced_deployment(namespace, limit=30)
    daemonsets = apps.list_namespaced_daemon_set(namespace, limit=20)
    statefulsets = apps.list_namespaced_stateful_set(namespace, limit=20)
    pods = core.list_namespaced_pod(namespace, limit=40)
    return {
        "namespace": namespace,
        "deployments": [
            {
                "name": d.metadata.name,
                "replicas": d.spec.replicas,
                "ready": d.status.ready_replicas,
                "unavailable": d.status.unavailable_replicas,
            }
            for d in deployments.items
        ],
        "daemonsets": [
            {
                "name": d.metadata.name,
                "desired": d.status.desired_number_scheduled,
                "ready": d.status.number_ready,
                "unavailable": d.status.number_unavailable,
            }
            for d in daemonsets.items
        ],
        "statefulsets": [
            {
                "name": s.metadata.name,
                "replicas": s.spec.replicas,
                "ready": s.status.ready_replicas,
            }
            for s in statefulsets.items
        ],
        "pods": [
            {
                "name": p.metadata.name,
                "phase": p.status.phase,
                "node": p.spec.node_name,
                "containers": [_container_state(c) for c in (p.status.container_statuses or [])],
            }
            for p in pods.items
        ],
    }


def inspect_pod(namespace: str, name: str) -> dict[str, Any]:
    if not valid_name(namespace) or not valid_name(name):
        raise ActionError("invalid namespace or name")
    client = _client()
    core = client.CoreV1Api()
    p = core.read_namespaced_pod(name, namespace)
    owners = [
        {"kind": o.kind, "name": o.name}
        for o in (p.metadata.owner_references or [])
    ]
    return {
        "name": p.metadata.name,
        "namespace": p.metadata.namespace,
        "phase": p.status.phase,
        "node": p.spec.node_name,
        "owners": owners,
        "qos": p.status.qos_class,
        "containers": [_container_state(c) for c in (p.status.container_statuses or [])],
        "conditions": [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": (c.message or "")[:200]}
            for c in (p.status.conditions or [])
        ],
    }


def inspect_logs(namespace: str, name: str, container: str = "", previous: bool = False) -> dict[str, Any]:
    if not valid_name(namespace) or not valid_name(name):
        raise ActionError("invalid namespace or name")
    if container and not valid_name(container):
        raise ActionError("invalid container name")
    client = _client()
    core = client.CoreV1Api()
    kwargs: dict[str, Any] = {
        "tail_lines": config.LOG_TAIL_LINES,
        "timestamps": True,
        "previous": previous,
    }
    if container:
        kwargs["container"] = container
    text = core.read_namespaced_pod_log(name, namespace, **kwargs)
    return {
        "namespace": namespace,
        "pod": name,
        "container": container or None,
        "previous": previous,
        "log": (text or "")[-config.TOOL_RESULT_MAX_CHARS :],
    }


def inspect_events(namespace: str, name: str = "") -> dict[str, Any]:
    if not valid_name(namespace):
        raise ActionError("invalid namespace")
    if name and not valid_name(name):
        raise ActionError("invalid name")
    client = _client()
    core = client.CoreV1Api()
    field_selector = f"involvedObject.name={name}" if name else None
    events = core.list_namespaced_event(namespace, field_selector=field_selector, limit=30)
    items = []
    for e in events.items:
        items.append(
            {
                "type": e.type,
                "reason": e.reason,
                "object": f"{getattr(e.involved_object, 'kind', '')}/{getattr(e.involved_object, 'name', '')}",
                "message": (e.message or "")[:300],
                "count": e.count,
                "last": str(e.last_timestamp or e.event_time or ""),
            }
        )
    return {"namespace": namespace, "events": items[:25]}


def inspect_workload(namespace: str, kind: str, name: str) -> dict[str, Any]:
    if not valid_name(namespace) or not valid_name(name):
        raise ActionError("invalid namespace or name")
    kind_l = kind.lower()
    client = _client()
    apps = client.AppsV1Api()
    if kind_l == "deployment":
        obj = apps.read_namespaced_deployment(name, namespace)
        spec_replicas = obj.spec.replicas
        ready = obj.status.ready_replicas
        images = [c.image for c in obj.spec.template.spec.containers]
        conditions = [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": (c.message or "")[:200]}
            for c in (obj.status.conditions or [])
        ]
    elif kind_l == "daemonset":
        obj = apps.read_namespaced_daemon_set(name, namespace)
        spec_replicas = obj.status.desired_number_scheduled
        ready = obj.status.number_ready
        images = [c.image for c in obj.spec.template.spec.containers]
        conditions = [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": (c.message or "")[:200]}
            for c in (obj.status.conditions or [])
        ]
    elif kind_l == "statefulset":
        obj = apps.read_namespaced_stateful_set(name, namespace)
        spec_replicas = obj.spec.replicas
        ready = obj.status.ready_replicas
        images = [c.image for c in obj.spec.template.spec.containers]
        conditions = [
            {"type": c.type, "status": c.status, "reason": c.reason, "message": (c.message or "")[:200]}
            for c in (obj.status.conditions or [])
        ]
    else:
        raise ActionError("kind must be Deployment, DaemonSet, or StatefulSet")
    return {
        "kind": kind,
        "namespace": namespace,
        "name": name,
        "replicas": spec_replicas,
        "ready": ready,
        "images": images,
        "conditions": conditions,
    }


def inspect_nodes() -> dict[str, Any]:
    client = _client()
    core = client.CoreV1Api()
    nodes = core.list_node()
    items = []
    for n in nodes.items:
        ready = "Unknown"
        for c in n.status.conditions or []:
            if c.type == "Ready":
                ready = c.status
        gpu = (n.status.capacity or {}).get("nvidia.com/gpu")
        items.append(
            {
                "name": n.metadata.name,
                "ready": ready,
                "unschedulable": bool(n.spec.unschedulable),
                "gpu": gpu,
                "roles": ",".join(
                    k.replace("node-role.kubernetes.io/", "")
                    for k in (n.metadata.labels or {})
                    if k.startswith("node-role.kubernetes.io/")
                ),
            }
        )
    return {"nodes": items}


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
