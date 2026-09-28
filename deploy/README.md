# Deploy

[`kubernetes/`](kubernetes/) is a single-replica install that talks to the cluster it runs in. Create the Secret before `kubectl apply -k deploy/kubernetes`. The Secret keys are listed in the [root README](../README.md).

Edit `kubernetes/configmap.yaml` before you apply it. `PUBLIC_BASE_URL`, `GITHUB_REPO`, and `CLUSTER_NAME` are placeholders.

The container image in the Deployment is `alert-processor:local`. Build it with:

```bash
podman build -t alert-processor:local -f Containerfile .
```

Replace that image with your registry if the nodes cannot see a local image.

OpenShift oauth-proxy, a Route, and the monitoring webhook are described in [`openshift/README.md`](openshift/README.md).
