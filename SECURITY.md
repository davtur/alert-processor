# Security

Alert processor can restart workloads and open pull requests after a person approves. Treat the deployment as privileged automation, not as a stateless web app.

## Reporting

Use [GitHub private vulnerability reporting](https://github.com/davtur/alert-processor/security/advisories/new).

Do not open a public issue, and do not include tokens, kubeconfigs, or alert payloads that contain customer data. Supported branch: `main`.

## Trust boundaries

| Surface | Who can call it | What it can do |
|---|---|---|
| `POST /api/v1/webhook` | Anyone who can reach the port, unless `WEBHOOK_TOKEN` is set | Store an alert and start a read-only investigation |
| Inbox API | Password session, or `X-Forwarded-User` / `X-Forwarded-Email` from a trusted proxy | Approve, reject, acknowledge, re-run the model |
| `GET/POST /t/{token}` | Holder of an HMAC link from email | Confirm the action named in the token |
| Approved runtime action | After the checks above | Restart one Deployment, delete one Pod, or scale one Deployment |
| GitHub token | The process | Push a branch and open a pull request on `GITHUB_REPO` |

The webhook does not execute those actions by itself. A person still has to approve. A forged webhook can still spend API quota, fill the inbox, and email you, so do not publish it.

## Deploy checklist

1. Set `SIGNING_SECRET` to a unique random value. The development default is logged on startup and must not be used on a shared network.
2. Set `WEBHOOK_TOKEN` and configure Alertmanager `http_config.bearer_token`. Leave the webhook off every public Route and Ingress. The example Service is ClusterIP for this reason.
3. Give the ServiceAccount the rules in [`deploy/kubernetes/rbac.yaml`](deploy/kubernetes/rbac.yaml), not `cluster-admin`.
4. Give `GITHUB_TOKEN` access only to the GitOps repository, limited to contents and pull requests. Do not grant administration or workflows.
5. Run one replica on SQLite, or set `DATABASE_URL` before scaling out. Two replicas on one SQLite file will corrupt it.
6. If you terminate TLS in a proxy, keep uvicorn's `--no-proxy-headers` (the image default). The app reads identity from `X-Forwarded-User` only when you trust that proxy to strip client-supplied copies of the header. oauth-proxy does this when it is the only hop in front of the container.
7. `openshift-*` and `kube-*` namespaces are writable only when the firing alert already names that namespace. That is a guardrail, not a substitute for RBAC.

Email approval links are bearer tokens. Anyone who can read the mailbox, or a forward of the message, can open the confirm page until the token expires (default 24 hours). Confirm is a POST so a mail gateway that prefetches GET links does not execute the action.

The model sees pod logs and events for namespaces it asks about. Do not point this at a cluster whose logs are more sensitive than the people who receive the email and can open the inbox.

## Out of scope

This project does not sandbox the model beyond the tool list in `app/investigate.py` and the action list in `app/k8s.py`. Proposals are text in a pull request. Review the diff before you merge it.
