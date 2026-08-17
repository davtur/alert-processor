"""Alert-processor HTTP API, Alertmanager webhook, and mobile PWA."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config, db, github_pr, grok, k8s, mailer, tokens

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("alert-processor")

STATIC_DIR = Path(__file__).resolve().parent / "static"
SESSION_COOKIE = "ap_session"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    log.info("alert-processor ready, db=%s", config.DB_PATH)
    yield


app = FastAPI(title="alert-processor", version="1.0.0", lifespan=lifespan)


class LoginBody(BaseModel):
    password: str = ""


class AlertmanagerWebhook(BaseModel):
    version: str | None = None
    groupKey: str | None = None
    status: str = "firing"
    receiver: str | None = None
    groupLabels: dict[str, Any] = Field(default_factory=dict)
    commonLabels: dict[str, Any] = Field(default_factory=dict)
    commonAnnotations: dict[str, Any] = Field(default_factory=dict)
    alerts: list[dict[str, Any]] = Field(default_factory=list)


def _session_ok(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE, "")
    return bool(token) and tokens.is_session_token(token)


def _require_session(request: Request) -> None:
    if not _session_ok(request):
        raise HTTPException(status_code=401, detail="login required")


def _should_skip(payload: dict[str, Any]) -> bool:
    alerts = payload.get("alerts") or []
    names = {
        (a.get("labels") or {}).get("alertname") or payload.get("commonLabels", {}).get("alertname")
        for a in alerts
    }
    names.discard(None)
    if not names:
        name = (payload.get("commonLabels") or {}).get("alertname")
        names = {name} if name else set()
    return bool(names) and names.issubset(config.SKIP_ALERTNAMES)


def _fingerprint(payload: dict[str, Any]) -> str:
    group_key = str(payload.get("groupKey") or "")
    if group_key:
        return group_key[:200]
    alerts = payload.get("alerts") or []
    if alerts and alerts[0].get("fingerprint"):
        return str(alerts[0]["fingerprint"])
    labels = payload.get("commonLabels") or {}
    return f"{labels.get('alertname', 'alert')}/{labels.get('namespace', '')}"


def _cooldown_active(incident: dict[str, Any]) -> bool:
    last = db.parse_iso(incident.get("last_notified_at"))
    if not last:
        return False
    age = datetime.now(timezone.utc) - last
    return age.total_seconds() < config.NOTIFY_COOLDOWN_SECONDS


def _process_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    if _should_skip(payload):
        return {"status": "skipped", "reason": "ignored alertname"}

    fingerprint = _fingerprint(payload)
    labels = payload.get("commonLabels") or {}
    alerts = payload.get("alerts") or []
    if not labels and alerts:
        labels = alerts[0].get("labels") or {}
    alertname = str(labels.get("alertname") or "unknown")
    namespace = str(labels.get("namespace") or "")
    severity = str(labels.get("severity") or "")
    am_status = str(payload.get("status") or "firing").lower()
    incident_status = "resolved" if am_status == "resolved" else "firing"

    incident = db.upsert_incident(
        fingerprint=fingerprint,
        group_key=str(payload.get("groupKey") or fingerprint),
        status=incident_status,
        alertname=alertname,
        namespace=namespace,
        severity=severity,
        payload=payload,
    )
    db.add_audit(incident["id"], "webhook", "alertmanager", am_status)

    if incident_status == "resolved":
        return {"status": "resolved", "id": incident["id"]}

    if incident.get("recommendation") and _cooldown_active(incident):
        return {"status": "deduplicated", "id": incident["id"]}

    context = k8s.cluster_context(namespace) if namespace else ""
    recommendation = grok.recommend(payload, context)
    db.save_recommendation(incident["id"], recommendation)
    incident = db.get_by_id(incident["id"]) or incident
    mailer.send_recommendation(incident, recommendation)
    db.mark_notified(incident["id"])
    return {"status": "processed", "id": incident["id"], "action_type": recommendation["action_type"]}


def _apply_decision(incident: dict[str, Any], action: str, actor: str) -> dict[str, Any]:
    if incident.get("status") in {"approved", "rejected"}:
        raise HTTPException(status_code=409, detail=f"already {incident['status']}")
    rec = incident.get("recommendation") or {}
    if action == "reject":
        db.set_status(incident["id"], "rejected", "Rejected by user")
        db.add_audit(incident["id"], "reject", actor, "")
        return {"status": "rejected", "id": incident["id"]}
    if action == "acknowledge":
        db.set_status(incident["id"], "acknowledged", "Acknowledged")
        db.add_audit(incident["id"], "acknowledge", actor, "")
        return {"status": "acknowledged", "id": incident["id"]}
    if action != "approve":
        raise HTTPException(status_code=400, detail="unknown action")

    action_type = rec.get("action_type") or "acknowledge"
    origin_ns = str(incident.get("namespace") or "")
    try:
        if action_type == "gitops_pr":
            result = github_pr.create_pr(incident, rec)
            db.set_status(incident["id"], "approved", result)
            db.add_audit(incident["id"], "approve", actor, result)
            mailer.send_action_result(
                incident,
                f"[APPROVED] {incident.get('alertname')} GitOps PR opened",
                f"PR: {result}\nThis will not merge automatically.",
            )
            return {"status": "approved", "id": incident["id"], "result": result, "pr_url": result}
        result = k8s.execute(rec, origin_ns)
        db.set_status(incident["id"], "approved", result)
        db.add_audit(incident["id"], "approve", actor, result)
        mailer.send_action_result(
            incident,
            f"[APPROVED] {incident.get('alertname')} {action_type}",
            result,
        )
        return {"status": "approved", "id": incident["id"], "result": result}
    except (k8s.ActionError, github_pr.GitOpsError) as exc:
        db.add_audit(incident["id"], "approve_failed", actor, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.exception("Approval execution failed")
        db.add_audit(incident["id"], "approve_failed", actor, str(exc))
        raise HTTPException(status_code=500, detail=f"execution failed: {exc}") from exc


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/v1/webhook")
async def webhook(body: AlertmanagerWebhook) -> dict[str, Any]:
    payload = body.model_dump()
    return _process_webhook(payload)


@app.post("/api/v1/login")
def login(body: LoginBody) -> JSONResponse:
    if not config.AUTH_PASSWORD:
        raise HTTPException(status_code=500, detail="AUTH_PASSWORD is not configured")
    import hmac

    if not hmac.compare_digest(body.password, config.AUTH_PASSWORD):
        raise HTTPException(status_code=401, detail="invalid password")
    response = JSONResponse({"status": "ok"})
    response.set_cookie(
        SESSION_COOKIE,
        tokens.make_session_token(),
        httponly=True,
        secure=config.PUBLIC_BASE_URL.startswith("https"),
        samesite="lax",
        max_age=config.SESSION_TTL_SECONDS,
        path="/",
    )
    return response


@app.post("/api/v1/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"status": "ok"})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@app.get("/api/v1/session")
def session(request: Request) -> dict[str, bool]:
    return {"authenticated": _session_ok(request)}


@app.get("/api/v1/incidents")
def incidents(request: Request, status: str | None = None) -> dict[str, Any]:
    _require_session(request)
    items = db.list_incidents(status=status)
    summaries = []
    for item in items:
        rec = item.get("recommendation") or {}
        summaries.append(
            {
                "id": item["id"],
                "status": item["status"],
                "alertname": item.get("alertname"),
                "namespace": item.get("namespace"),
                "severity": item.get("severity"),
                "updated_at": item.get("updated_at"),
                "summary": rec.get("summary"),
                "action_type": rec.get("action_type"),
                "risk": rec.get("risk"),
            }
        )
    return {"incidents": summaries}


@app.get("/api/v1/incidents/{incident_id}")
def incident_detail(request: Request, incident_id: int) -> dict[str, Any]:
    _require_session(request)
    item = db.get_by_id(incident_id)
    if not item:
        raise HTTPException(status_code=404, detail="not found")
    return item


@app.post("/api/v1/incidents/{incident_id}/approve")
def api_approve(request: Request, incident_id: int) -> dict[str, Any]:
    _require_session(request)
    item = db.get_by_id(incident_id)
    if not item:
        raise HTTPException(status_code=404, detail="not found")
    return _apply_decision(item, "approve", "ui")


@app.post("/api/v1/incidents/{incident_id}/reject")
def api_reject(request: Request, incident_id: int) -> dict[str, Any]:
    _require_session(request)
    item = db.get_by_id(incident_id)
    if not item:
        raise HTTPException(status_code=404, detail="not found")
    return _apply_decision(item, "reject", "ui")


@app.post("/api/v1/incidents/{incident_id}/acknowledge")
def api_ack(request: Request, incident_id: int) -> dict[str, Any]:
    _require_session(request)
    item = db.get_by_id(incident_id)
    if not item:
        raise HTTPException(status_code=404, detail="not found")
    return _apply_decision(item, "acknowledge", "ui")


def _token_page(token: str, error: str = "", payload: dict[str, Any] | None = None, result: dict[str, Any] | None = None) -> str:
    incident = None
    if payload:
        incident = db.get_by_id(int(payload.get("id", 0)))
    rec = (incident or {}).get("recommendation") or {}
    action = (payload or {}).get("act") or ""
    title = "Alert processor"
    body = ""
    if error:
        body = f"<p class='err'>{error}</p>"
    elif result:
        body = f"<p class='ok'>Done: {result.get('status')}</p><p>{result.get('result') or result.get('pr_url') or ''}</p>"
    elif incident:
        body = f"""
        <p class="eyebrow">{incident.get('namespace') or '-'} · {incident.get('severity') or '-'}</p>
        <h1>{incident.get('alertname')}</h1>
        <p>{rec.get('summary') or ''}</p>
        <p class="muted">{rec.get('root_cause') or ''}</p>
        <p><strong>Action:</strong> {rec.get('action_type')}</p>
        <form method="post" action="/t/{token}">
          <button class="primary" type="submit">Confirm {action}</button>
        </form>
        <p class="muted">Email clients prefetch GET links, so nothing runs until you confirm.</p>
        """
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
  <title>{title}</title>
  <link rel="stylesheet" href="/static/styles.css"/>
</head>
<body class="page">
  <main class="sheet">{body}<p><a href="/">Open inbox</a></p></main>
</body>
</html>
"""


@app.get("/t/{token}", response_class=HTMLResponse)
def token_get(token: str) -> HTMLResponse:
    try:
        payload = tokens.decode(token)
    except tokens.TokenError as exc:
        return HTMLResponse(_token_page(token, error=str(exc)), status_code=400)
    return HTMLResponse(_token_page(token, payload=payload))


@app.post("/t/{token}", response_class=HTMLResponse)
def token_post(token: str) -> HTMLResponse:
    try:
        payload = tokens.decode(token)
    except tokens.TokenError as exc:
        return HTMLResponse(_token_page(token, error=str(exc)), status_code=400)
    item = db.get_by_id(int(payload.get("id", 0)))
    if not item:
        return HTMLResponse(_token_page(token, error="incident not found"), status_code=404)
    action = str(payload.get("act") or "")
    try:
        result = _apply_decision(item, action, "email")
    except HTTPException as exc:
        return HTMLResponse(_token_page(token, error=str(exc.detail), payload=payload), status_code=exc.status_code)
    return HTMLResponse(_token_page(token, payload=payload, result=result))


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
