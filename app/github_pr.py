"""Open a GitHub PR on openshift-delta for a proposed GitOps change."""

from __future__ import annotations

import base64
import logging
import re
from typing import Any

import httpx

from app import config

log = logging.getLogger("alert-processor.github")


class GitOpsError(ValueError):
    pass


_BRANCH_SAFE = re.compile(r"[^a-zA-Z0-9._/-]+")
_FENCE = re.compile(r"^```(?:ya?ml|diff)?\s*\n(.*)\n```\s*$", re.DOTALL | re.IGNORECASE)


def validate_path(path: str) -> str:
    path = path.lstrip("/")
    if not path or ".." in path or path.startswith("."):
        raise GitOpsError("invalid gitops path")
    if not path.endswith((".yaml", ".yml", ".md")):
        raise GitOpsError("gitops path must be yaml or markdown")
    if not path.startswith(config.GITOPS_PATH_PREFIXES):
        raise GitOpsError(
            "gitops path must be under apps-kustomize/, cluster-kustomize/, "
            "operator-subscriptions/, apps-argo/, or gitops-oai/"
        )
    return path


def yaml_body(content: str) -> str:
    text = (content or "").strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    elif text.startswith("```"):
        text = re.sub(r"^```(?:ya?ml|diff)?\s*\n?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\n```\s*$", "", text)
        text = text.strip()
    return (text + "\n") if text else ""


def has_yaml_proposal(rec: dict[str, Any]) -> bool:
    gitops = rec.get("gitops") or {}
    body = yaml_body(str(gitops.get("yaml_or_patch") or ""))
    meaningful = [
        line
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return bool(meaningful)


def resolve_path(incident: dict[str, Any], rec: dict[str, Any]) -> str:
    gitops = rec.get("gitops") or {}
    raw = str(gitops.get("path") or "").strip()
    try:
        return validate_path(raw)
    except GitOpsError:
        alert = _BRANCH_SAFE.sub("-", str(incident.get("alertname") or "alert")).strip("-")
        alert = (alert or "alert").lower()[:40]
        return f"apps-kustomize/alert-processor/proposals/{incident['id']}-{alert}.yaml"


def _headers() -> dict[str, str]:
    if not config.GITHUB_TOKEN:
        raise GitOpsError("GITHUB_TOKEN is not set")
    return {
        "Authorization": f"Bearer {config.GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "alert-processor",
    }


def _api(method: str, path: str, **kwargs: Any) -> httpx.Response:
    url = f"{config.GITHUB_API}{path}"
    with httpx.Client(timeout=45.0) as client:
        response = client.request(method, url, headers=_headers(), **kwargs)
    if response.status_code >= 400:
        raise GitOpsError(f"GitHub API {response.status_code}: {response.text[:500]}")
    return response


def _existing_pr_url(repo: str, branch: str) -> str:
    owner = repo.split("/")[0]
    pulls = _api(
        "GET",
        f"/repos/{repo}/pulls",
        params={"head": f"{owner}:{branch}", "state": "open"},
    ).json()
    if isinstance(pulls, list) and pulls:
        return str(pulls[0].get("html_url") or "")
    return ""


def _file_sha(repo: str, path: str, ref: str) -> str | None:
    try:
        existing = _api("GET", f"/repos/{repo}/contents/{path}", params={"ref": ref}).json()
        return existing.get("sha")
    except GitOpsError:
        return None


def ensure_pr(incident: dict[str, Any], rec: dict[str, Any]) -> str:
    existing = str(rec.get("pr_url") or "").strip()
    if existing.startswith("https://"):
        return existing
    return create_pr(incident, rec)


def create_pr(incident: dict[str, Any], rec: dict[str, Any]) -> str:
    gitops = rec.get("gitops") or {}
    path = resolve_path(incident, rec)
    content = yaml_body(str(gitops.get("yaml_or_patch") or ""))
    if not content.strip():
        raise GitOpsError("gitops yaml_or_patch is empty")

    fingerprint = str(incident.get("fingerprint") or incident["id"])
    branch = _BRANCH_SAFE.sub("-", f"alert/{fingerprint}")[:80]
    repo = config.GITHUB_REPO
    alertname = incident.get("alertname") or "alert"

    repo_info = _api("GET", f"/repos/{repo}").json()
    default_branch = repo_info.get("default_branch") or "main"
    ref = _api("GET", f"/repos/{repo}/git/ref/heads/{default_branch}").json()
    base_sha = ref["object"]["sha"]

    try:
        _api(
            "POST",
            f"/repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
    except GitOpsError as exc:
        if "Reference already exists" not in str(exc):
            raise
        existing = _existing_pr_url(repo, branch)
        if existing:
            sha = _file_sha(repo, path, branch)
            encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
            put_body: dict[str, Any] = {
                "message": f"fix: update alert-processor proposal for {alertname}",
                "content": encoded,
                "branch": branch,
            }
            if sha:
                put_body["sha"] = sha
            _api("PUT", f"/repos/{repo}/contents/{path}", json=put_body)
            log.info("Updated existing GitOps PR %s", existing)
            return existing
        branch = f"{branch}-{incident['id']}"
        _api(
            "POST",
            f"/repos/{repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )

    sha = _file_sha(repo, path, default_branch)
    message = f"fix: alert-processor proposal for {alertname}"
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    put_body: dict[str, Any] = {
        "message": message,
        "content": encoded,
        "branch": branch,
    }
    if sha:
        put_body["sha"] = sha
    _api("PUT", f"/repos/{repo}/contents/{path}", json=put_body)

    existing = _existing_pr_url(repo, branch)
    if existing:
        log.info("Reused GitOps PR %s", existing)
        return existing

    rationale = gitops.get("rationale") or rec.get("summary") or ""
    try:
        pr = _api(
            "POST",
            f"/repos/{repo}/pulls",
            json={
                "title": f"fix: {alertname} (alert-processor)",
                "head": branch,
                "base": default_branch,
                "body": (
                    f"Opened by alert-processor for incident `{incident['id']}` "
                    f"(fingerprint `{fingerprint}`).\n\n"
                    f"**Recommendation:** {rec.get('summary')}\n\n"
                    f"**Rationale:** {rationale}\n\n"
                    f"**Proposed file:** `{path}`\n\n"
                    f"**Risk analysis**\n"
                    f"- **Risk rating:** 2\n"
                    f"- **Why:** Automated proposal from a firing alert. Review the diff "
                    f"before merge; this is not auto-applied to the cluster.\n"
                ),
            },
        ).json()
    except GitOpsError as exc:
        existing = _existing_pr_url(repo, branch)
        if existing:
            return existing
        raise
    html_url = pr.get("html_url") or ""
    log.info("Opened GitOps PR %s", html_url)
    return html_url
