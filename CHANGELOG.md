# Changelog

## 1.0.0 - 2026-09-28

First release intended for other clusters.

- Alertmanager webhook, read-only Grok investigation, and a human approval step.
- Approved actions are restart Deployment, delete one Pod, scale Deployment, or open a GitOps pull request.
- Inbox works with a shared password or with identity headers from a reverse proxy such as OpenShift oauth-proxy.
- SQLite by default, PostgreSQL when `DATABASE_URL` is set.
- Repository, mail, and cluster name come from the environment. See `.env.example`.
