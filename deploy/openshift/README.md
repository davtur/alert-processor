# OpenShift

The Kubernetes manifests in [`../kubernetes/`](../kubernetes/) run on OpenShift. Two extra pieces are specific to OpenShift.

## Inbox route

Put oauth-proxy in the same pod, listening on 4180, with `--upstream=http://127.0.0.1:8080` and `--pass-user-headers=true`. Point the Route at port 4180.

Skip authentication only for health and for the signed email links:

```text
--skip-auth-regex=^/(healthz$|readyz$|t/|static/console-icon\.(svg|png)$)
```

Do not add `/api/v1/webhook` to that regex. Alertmanager should call the Service on port 8080, which bypasses the proxy. The app then sees `X-Forwarded-User` for people who logged in through OpenShift, and the password form is only for local runs.

The ServiceAccount needs `system:auth-delegator` so oauth-proxy can authenticate, and it needs to read the serving cert ConfigMap in `openshift-config-managed`. Use the oauth-proxy image from your cluster payload (`oc get imagestream oauth-proxy -n openshift`) rather than pinning a digest from another cluster.

Set these on the application container, not only in the proxy:

- `PUBLIC_BASE_URL` — the Route origin
- `OAUTH_LOGOUT_URL` — the cluster logout URL with `then=` set back to that Route
- `OAUTH_COOKIE_NAME` — the same name the proxy uses, default `_oauth_proxy_ap`
- `CLUSTER_NAME` — label in the inbox header

A console link can point at the Route so the Application menu opens the inbox. Add to Home Screen from the phone browser if you want the installable app.

## Build

A BuildConfig with `dockerStrategy.dockerfilePath: Containerfile` builds this repository into an ImageStream. The image listens on 8080 and runs as UID 1001.

## Webhook

Apply [`../alertmanager-webhook.example.yaml`](../alertmanager-webhook.example.yaml) only after `WEBHOOK_TOKEN` exists on the `alert-processor` Secret. OpenShift monitoring reads AlertmanagerConfig from the namespace named in the resource.
