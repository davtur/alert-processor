# Alert processor

Receives OpenShift Alertmanager webhooks, asks xAI Grok for a remediation recommendation, then asks you to approve it from an iPhone web app or Gmail.

Permanent cluster changes open a pull request on [davtur/openshift-delta](https://github.com/davtur/openshift-delta). Runtime actions are limited to restarting a Deployment, deleting a named Pod, or scaling (capped). Nothing is auto-merged and there is no freeform shell.

GitOps manifests live in `openshift-delta` under `apps-kustomize/alert-processor/` and `apps-argo/apps/alert-processor.yaml`.

## Local run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export XAI_API_KEY=...
export AUTH_PASSWORD=...
export SIGNING_SECRET=...
export DATA_DIR=./data
export PUBLIC_BASE_URL=http://127.0.0.1:8080
uvicorn app.main:app --reload --port 8080
```

Open http://127.0.0.1:8080 and log in with `AUTH_PASSWORD`.

Simulate Alertmanager:

```bash
curl -sS http://127.0.0.1:8080/api/v1/webhook \
  -H 'Content-Type: application/json' \
  -d '{"status":"firing","groupKey":"{}:{alertname=KubePodCrashLooping}","commonLabels":{"alertname":"KubePodCrashLooping","namespace":"demo","severity":"warning"},"alerts":[{"status":"firing","labels":{"alertname":"KubePodCrashLooping","namespace":"demo","severity":"warning","pod":"web-0"},"annotations":{"summary":"Pod crash looping"},"fingerprint":"abc"}]}'
```

`Watchdog` and `InfoInhibitor` are ignored.

## Environment

| Variable | Purpose |
|---|---|
| `XAI_API_KEY` | xAI Grok API key |
| `XAI_MODEL` | default `grok-4-1-fast-non-reasoning` |
| `SMTP_PASSWORD` | Gmail app password for `davtur@gmail.com` |
| `GITHUB_TOKEN` | PAT with `repo` on `davtur/openshift-delta` |
| `AUTH_PASSWORD` | Shared password for the mobile inbox |
| `SIGNING_SECRET` | HMAC secret for email tokens and session cookies |
| `PUBLIC_BASE_URL` | Public Route URL used in email links |
| `DATA_DIR` | SQLite directory (PVC `/data` in-cluster) |

## Approval flow

1. Alertmanager posts to the in-cluster webhook (not on the Route).
2. Grok returns JSON (`restart_deployment`, `delete_pod`, `scale_deployment`, `gitops_pr`, or `acknowledge`).
3. Gmail gets Review/Reject links. Those pages confirm with POST so mail prefetch cannot execute.
4. The PWA at `https://alert-processor.apps.delta.drtsoft.com` lists firing alerts. Add to iPhone Home Screen from Safari.

## Image

OpenShift builds from this repo with a Docker-strategy BuildConfig (`Containerfile` / `Dockerfile`) into ImageStream `alert-processor:latest`.
