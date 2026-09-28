"""Whitelisted Kubernetes runtime actions via the in-cluster API."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from app import catalog, config

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
        "log": (text or "")[-catalog.tool_result_max_chars() :],
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


def _truncate(value: Any, limit: int = 200) -> str:
    return str(value or "")[:limit]


def _condition_summary(conditions: list[Any] | None, message_limit: int = 200) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for c in conditions or []:
        if isinstance(c, dict):
            items.append(
                {
                    "type": str(c.get("type") or ""),
                    "status": str(c.get("status") or ""),
                    "reason": str(c.get("reason") or ""),
                    "message": _truncate(c.get("message"), message_limit),
                }
            )
        else:
            items.append(
                {
                    "type": str(getattr(c, "type", "") or ""),
                    "status": str(getattr(c, "status", "") or ""),
                    "reason": str(getattr(c, "reason", "") or ""),
                    "message": _truncate(getattr(c, "message", ""), message_limit),
                }
            )
    return items


def _argocd_source_summary(spec: dict[str, Any]) -> dict[str, str]:
    source = spec.get("source") if isinstance(spec.get("source"), dict) else {}
    if not source:
        sources = spec.get("sources")
        if isinstance(sources, list) and sources and isinstance(sources[0], dict):
            source = sources[0]
    return {
        "repoURL": str(source.get("repoURL") or ""),
        "path": str(source.get("path") or ""),
        "targetRevision": str(source.get("targetRevision") or ""),
    }


def inspect_argocd_application(namespace: str, name: str) -> dict[str, Any]:
    if not valid_name(namespace) or not valid_name(name):
        raise ActionError("invalid namespace or name")
    client = _client()
    custom = client.CustomObjectsApi()
    obj = custom.get_namespaced_custom_object(
        group="argoproj.io",
        version="v1alpha1",
        namespace=namespace,
        plural="applications",
        name=name,
    )
    spec = obj.get("spec") if isinstance(obj.get("spec"), dict) else {}
    status = obj.get("status") if isinstance(obj.get("status"), dict) else {}
    sync = status.get("sync") if isinstance(status.get("sync"), dict) else {}
    health = status.get("health") if isinstance(status.get("health"), dict) else {}
    metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    source = _argocd_source_summary(spec)
    problem_resources: list[dict[str, Any]] = []
    for res in status.get("resources") or []:
        if not isinstance(res, dict):
            continue
        res_sync = str(res.get("status") or "")
        res_health = res.get("health") if isinstance(res.get("health"), dict) else {}
        res_health_status = str(res_health.get("status") or "")
        if res_sync == "Synced" and res_health_status == "Healthy":
            continue
        problem_resources.append(
            {
                "kind": str(res.get("kind") or ""),
                "namespace": str(res.get("namespace") or ""),
                "name": str(res.get("name") or ""),
                "status": res_sync,
                "health": res_health_status or None,
            }
        )
        if len(problem_resources) >= 15:
            break
    return {
        "name": str(metadata.get("name") or name),
        "namespace": str(metadata.get("namespace") or namespace),
        "project": str(spec.get("project") or ""),
        "repoURL": source["repoURL"],
        "path": source["path"],
        "targetRevision": source["targetRevision"],
        "sync": str(sync.get("status") or ""),
        "health": str(health.get("status") or ""),
        "conditions": [
            {"type": c.get("type", ""), "message": _truncate(c.get("message"), 200)}
            for c in (status.get("conditions") or [])
            if isinstance(c, dict)
        ][:10],
        "problem_resources": problem_resources,
    }


def list_argocd_applications(namespace: str = "") -> dict[str, Any]:
    if namespace and not valid_name(namespace):
        raise ActionError("invalid namespace")
    client = _client()
    custom = client.CustomObjectsApi()
    if namespace:
        result = custom.list_namespaced_custom_object(
            group="argoproj.io",
            version="v1alpha1",
            namespace=namespace,
            plural="applications",
        )
    else:
        result = custom.list_cluster_custom_object(
            group="argoproj.io",
            version="v1alpha1",
            plural="applications",
        )
    items: list[dict[str, str]] = []
    for obj in (result.get("items") or [])[:40]:
        if not isinstance(obj, dict):
            continue
        metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
        status = obj.get("status") if isinstance(obj.get("status"), dict) else {}
        sync = status.get("sync") if isinstance(status.get("sync"), dict) else {}
        health = status.get("health") if isinstance(status.get("health"), dict) else {}
        items.append(
            {
                "namespace": str(metadata.get("namespace") or namespace or ""),
                "name": str(metadata.get("name") or ""),
                "sync": str(sync.get("status") or ""),
                "health": str(health.get("status") or ""),
            }
        )
    return {"applications": items, "count": len(items)}


def _cluster_operator_unhealthy(conditions: list[dict[str, Any]]) -> bool:
    by_type = {str(c.get("type") or ""): c for c in conditions if isinstance(c, dict)}
    available = by_type.get("Available") or {}
    degraded = by_type.get("Degraded") or {}
    progressing = by_type.get("Progressing") or {}
    if str(available.get("status") or "") != "True":
        return True
    if str(degraded.get("status") or "") == "True":
        return True
    if str(progressing.get("status") or "") == "True":
        return True
    return False


def inspect_cluster_operator(name: str = "") -> dict[str, Any]:
    if name and not valid_name(name):
        raise ActionError("invalid name")
    client = _client()
    custom = client.CustomObjectsApi()
    if name:
        obj = custom.get_cluster_custom_object(
            group="config.openshift.io",
            version="v1",
            plural="clusteroperators",
            name=name,
        )
        status = obj.get("status") if isinstance(obj.get("status"), dict) else {}
        metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
        return {
            "name": str(metadata.get("name") or name),
            "conditions": _condition_summary(status.get("conditions"), message_limit=200),
        }
    result = custom.list_cluster_custom_object(
        group="config.openshift.io",
        version="v1",
        plural="clusteroperators",
    )
    items: list[dict[str, Any]] = []
    for obj in result.get("items") or []:
        if not isinstance(obj, dict):
            continue
        status = obj.get("status") if isinstance(obj.get("status"), dict) else {}
        conditions = [c for c in (status.get("conditions") or []) if isinstance(c, dict)]
        if not _cluster_operator_unhealthy(conditions):
            continue
        metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
        items.append(
            {
                "name": str(metadata.get("name") or ""),
                "conditions": _condition_summary(conditions, message_limit=160),
            }
        )
        if len(items) >= 20:
            break
    return {"operators": items, "count": len(items)}


def _mcp_summary(obj: dict[str, Any]) -> dict[str, Any]:
    metadata = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
    status = obj.get("status") if isinstance(obj.get("status"), dict) else {}
    return {
        "name": str(metadata.get("name") or ""),
        "machineCount": status.get("machineCount"),
        "readyMachineCount": status.get("readyMachineCount"),
        "updatedMachineCount": status.get("updatedMachineCount"),
        "degradedMachineCount": status.get("degradedMachineCount"),
        "conditions": _condition_summary(status.get("conditions"), message_limit=200),
    }


def inspect_machine_config_pool(name: str = "") -> dict[str, Any]:
    if name and not valid_name(name):
        raise ActionError("invalid name")
    client = _client()
    custom = client.CustomObjectsApi()
    if name:
        obj = custom.get_cluster_custom_object(
            group="machineconfiguration.openshift.io",
            version="v1",
            plural="machineconfigpools",
            name=name,
        )
        return _mcp_summary(obj)
    result = custom.list_cluster_custom_object(
        group="machineconfiguration.openshift.io",
        version="v1",
        plural="machineconfigpools",
    )
    items = [_mcp_summary(obj) for obj in (result.get("items") or []) if isinstance(obj, dict)]
    return {"pools": items[:10], "count": min(len(items), 10)}


def inspect_job(namespace: str, name: str) -> dict[str, Any]:
    if not valid_name(namespace) or not valid_name(name):
        raise ActionError("invalid namespace or name")
    client = _client()
    batch = client.BatchV1Api()
    job = batch.read_namespaced_job(name, namespace)
    status = job.status
    return {
        "namespace": namespace,
        "name": name,
        "succeeded": status.succeeded,
        "failed": status.failed,
        "active": status.active,
        "completionTime": str(status.completion_time) if status.completion_time else None,
        "conditions": _condition_summary(status.conditions, message_limit=200),
    }


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
