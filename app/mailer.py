"""Gmail SMTP notifications with signed approval links."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from xml.sax.saxutils import escape

from app import config, tokens

log = logging.getLogger("alert-processor.mailer")


def _html(incident: dict[str, Any], rec: dict[str, Any], approve_url: str, reject_url: str) -> str:
    alertname = escape(str(incident.get("alertname") or "Alert"))
    namespace = escape(str(incident.get("namespace") or "-"))
    severity = escape(str(incident.get("severity") or "-"))
    summary = escape(str(rec.get("summary") or ""))
    root_cause = escape(str(rec.get("root_cause") or ""))
    risk = escape(str(rec.get("risk") or ""))
    action = escape(str(rec.get("action_type") or "acknowledge"))
    target = rec.get("target") or {}
    target_txt = escape(
        f"{target.get('kind', '')} {target.get('namespace', '')}/{target.get('name', '')}".strip()
    )
    inbox = escape(f"{config.PUBLIC_BASE_URL}/")
    pr_url = str(rec.get("pr_url") or "").strip()
    pr_error = str(rec.get("pr_error") or "").strip()
    if pr_url:
        pr_html = (
            f'<p style="margin:0 0 20px;"><strong>GitOps pull request</strong><br>'
            f'<a href="{escape(pr_url)}" style="color:#8fd19e;">{escape(pr_url)}</a></p>'
        )
    elif pr_error:
        pr_html = (
            f'<p style="color:#ff6b6b;margin:0 0 20px;">GitOps PR failed: {escape(pr_error)}</p>'
        )
    else:
        pr_html = ""
    return f"""<!DOCTYPE html>
<html>
<body style="font-family:-apple-system,Helvetica,Arial,sans-serif;background:#111;color:#f2f2f2;padding:24px;">
  <div style="max-width:520px;margin:0 auto;background:#1b1b1b;border-radius:16px;padding:24px;">
    <p style="color:#ee0000;font-weight:700;letter-spacing:.08em;text-transform:uppercase;margin:0 0 8px;">Alert processor</p>
    <h1 style="font-size:22px;margin:0 0 12px;">{alertname}</h1>
    <p style="color:#b4b4b4;margin:0 0 16px;">{namespace} · {severity} · risk {risk}</p>
    <p style="margin:0 0 8px;"><strong>Recommendation</strong></p>
    <p style="margin:0 0 12px;">{summary}</p>
    <p style="color:#b4b4b4;margin:0 0 12px;">{root_cause}</p>
    <p style="margin:0 0 20px;"><strong>Proposed action:</strong> {action}<br>{target_txt}</p>
    {pr_html}
    <p>
      <a href="{approve_url}" style="display:inline-block;background:#ee0000;color:#fff;text-decoration:none;padding:12px 18px;border-radius:10px;margin-right:8px;">Review &amp; approve</a>
      <a href="{reject_url}" style="display:inline-block;background:#3c3c3c;color:#fff;text-decoration:none;padding:12px 18px;border-radius:10px;">Reject</a>
    </p>
    <p style="margin-top:20px;"><a href="{inbox}" style="color:#b4b4b4;">Open inbox on iPhone</a></p>
  </div>
</body>
</html>
"""


def send_recommendation(incident: dict[str, Any], rec: dict[str, Any]) -> bool:
    if not config.SMTP_PASSWORD:
        log.warning("SMTP_PASSWORD is not set; skipping email")
        return False
    incident_id = int(incident["id"])
    approve = f"{config.PUBLIC_BASE_URL}/t/{tokens.make_action_token(incident_id, 'approve')}"
    reject = f"{config.PUBLIC_BASE_URL}/t/{tokens.make_action_token(incident_id, 'reject')}"
    alertname = incident.get("alertname") or "alert"
    subject = f"[FIRING] {alertname} — Grok recommendation needs approval"
    html = _html(incident, rec, approve, reject)
    lines = [
        str(alertname),
        str(rec.get("summary") or ""),
        "",
        f"Action: {rec.get('action_type')}",
    ]
    if rec.get("pr_url"):
        lines.append(f"PR: {rec['pr_url']}")
    elif rec.get("pr_error"):
        lines.append(f"GitOps PR failed: {rec['pr_error']}")
    lines.extend([f"Approve: {approve}", f"Reject: {reject}", ""])
    text = "\n".join(lines)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.MAIL_FROM
    msg["To"] = config.MAIL_TO
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.sendmail(config.MAIL_FROM, [config.MAIL_TO], msg.as_string())
        log.info("Sent recommendation email for incident %s", incident_id)
        return True
    except Exception:
        log.exception("Failed to send email for incident %s", incident_id)
        return False


def send_action_result(incident: dict[str, Any], title: str, body: str) -> None:
    if not config.SMTP_PASSWORD:
        return
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = title
    msg["From"] = config.MAIL_FROM
    msg["To"] = config.MAIL_TO
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.sendmail(config.MAIL_FROM, [config.MAIL_TO], msg.as_string())
    except Exception:
        log.exception("Failed to send follow-up email")
