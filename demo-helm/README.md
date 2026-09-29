# ai-obs-demo Helm chart

Deploys the full demo stack for
**"Why Is This Slow, Expensive, and Wrong? Observability Across the AI Stack"**

---

## What gets deployed

| Component        | Chart dependency              | Purpose                              |
|------------------|-------------------------------|--------------------------------------|
| Demo app         | (custom template)             | FastAPI Layer 3 app, quality scorer  |
| LiteLLM proxy    | (custom template)             | Model router, cost tracking          |
| vLLM             | (custom template)             | LLM inference runtime                |
| NATS JetStream   | nats/nats                     | Inference queue (Layer 2)            |
| DCGM exporter    | (custom DaemonSet)            | GPU metrics                          |
| Gravitee APIM    | gravitee/apim                 | API gateway, key enforcement (Layer 1) |
| Grafana          | grafana/grafana               | Dashboards                           |
| Loki             | grafana/loki                  | Log storage                          |
| Tempo            | grafana/tempo                 | Distributed trace storage            |
| Alloy            | grafana/alloy                 | OTel collector + metric scraper      |
| Prometheus       | prometheus-community/prometheus | Metrics storage                    |
| Langfuse         | langfuse/langfuse             | LLM trace + quality score storage    |

Gravitee APIM and its dependencies (MongoDB, Elasticsearch) are bundled as subcharts
of the apim chart, gated on `gravitee.enabled`. Disable them if you don't need Layer 1.

---

## Prerequisites

- Kubernetes 1.26+
- Helm 3.12+
- NVIDIA GPU node with drivers installed (or use `values-local.yaml` for CPU-only rehearsal)
- **NVIDIA device plugin** — required for Kubernetes to see GPUs (see below)
- A HuggingFace token if using a gated model

### GPU setup (k3s)

k3s ships with the `nvidia` RuntimeClass but does **not** include the device plugin.
Install it before deploying the demo chart:

```bash
helm repo add nvdp https://nvidia.github.io/k8s-device-plugin
helm repo update
helm install nvdp nvdp/nvidia-device-plugin \
  --namespace kube-system \
  --set runtimeClassName=nvidia \
  --set-json 'affinity={}'
```

The `affinity={}` override disables the default NFD node-label requirement.
Verify GPUs are advertised:

```bash
kubectl get nodes -o json | jq '.items[].status.capacity | with_entries(select(.key | contains("nvidia")))'
```

You should see `"nvidia.com/gpu": "N"` where N matches your GPU count.

---

## Quick start

```bash
# 1. Add dependent chart repos
helm repo add grafana    https://grafana.github.io/helm-charts
helm repo add prometheus https://prometheus-community.github.io/helm-charts
helm repo add nats       https://nats-io.github.io/k8s/helm/charts
helm repo add langfuse   https://langfuse.github.io/langfuse-k8s
helm repo add bitnami    https://charts.bitnami.com/bitnami
helm repo add elastic    https://helm.elastic.co
helm repo add gravitee   https://helm.gravitee.io
helm repo update

# 2. Pull dependencies
helm dependency update

# 3. Cluster-specific settings (Langfuse URL, NetBird, tenant keys) — git-ignored
cp values-override.example.yaml values-override.yaml

# 4. Install (demo cluster with GPU)
helm install ai-obs-demo . \
  -f values.yaml \
  -f values-override.yaml \
  --namespace ai-obs-demo \
  --create-namespace \
  --set vllm.hfToken=hf_yourtoken \
  --set demoApp.langfuse.publicKey=pk-lf-yourkey \
  --set demoApp.langfuse.secretKey=sk-lf-yourkey

# 5. Local rehearsal without GPU
helm install ai-obs-demo . \
  -f values.yaml \
  -f values-local.yaml \
  -f values-override.yaml \
  --namespace ai-obs-demo \
  --create-namespace
```

---

## Accessing services after install

Helm prints port-forward commands automatically after install.
Or run them all at once:

```bash
kubectl port-forward svc/ai-obs-demo-grafana           3000:80   -n ai-obs-demo &
kubectl port-forward svc/ai-obs-demo-langfuse-web      3001:3000 -n ai-obs-demo &
kubectl port-forward svc/ai-obs-demo-prometheus-server 9090:80   -n ai-obs-demo &
kubectl port-forward svc/ai-obs-demo-tempo             3200:3100 -n ai-obs-demo &
kubectl port-forward svc/ai-obs-demo-app               8080:8080 -n ai-obs-demo &
kubectl port-forward svc/ai-obs-demo-litellm           4000:4000 -n ai-obs-demo &
# Gravitee (if enabled)
kubectl port-forward svc/ai-obs-demo-gravitee-gateway  8082:8082 -n ai-obs-demo &
kubectl port-forward svc/ai-obs-demo-gravitee-ui       8002:8002 -n ai-obs-demo &
```

---

## Gravitee bootstrap

A post-install Job (`gravitee-bootstrap.yaml`) runs automatically after `helm install`
or `helm upgrade`. It:

1. Waits for the Gravitee Management API to become available
2. Creates the demo API with an API Key plan
3. Creates one application per tenant and subscribes each
4. Logs the auto-generated API keys — copy them into your `.env`

Watch progress:
```bash
kubectl logs -l job-name=ai-obs-demo-gravitee-bootstrap -n ai-obs-demo -f
```

If the custom key PUT fails, the Job logs the generated key. Update
`TENANT_*_API_KEY` in your `.env` to match before running the orchestrator.

---

## Langfuse persistence

PostgreSQL and ClickHouse **must** have `persistence.enabled: true` in `values.yaml`.
Without persistence, data lives on `emptyDir` and is wiped on every pod restart —
including Prisma migration state and all Langfuse trace data.

If you need to switch from `emptyDir` to PVC on an existing install, delete the
StatefulSets first (Kubernetes forbids changing `volumeClaimTemplates` in-place):

```bash
kubectl delete statefulset ai-obs-demo-langfuse-postgresql -n ai-obs-demo --cascade=orphan
kubectl delete pod ai-obs-demo-langfuse-postgresql-0 -n ai-obs-demo
helm upgrade ...  # recreates with PVC
kubectl rollout restart deployment/ai-obs-demo-langfuse-web -n ai-obs-demo
```

---

## Important values to override

| Key                              | Default                  | Notes                                  |
|----------------------------------|--------------------------|----------------------------------------|
| `vllm.model`                     | `Qwen/Qwen3.5-9B`       | Any vLLM-supported model       |
| `vllm.hfToken`                   | `""`                     | Required for gated HF models           |
| `vllm.gpu.count`                 | `1`                      | GPUs per pod                           |
| `vllm.modelCache.type`           | `emptyDir`               | `emptyDir`, `hostPath`, or `pvc`       |
| `vllm.modelCache.hostPath`       | `/opt/models/huggingface`| Host path when type=hostPath           |
| `vllm.nodeSelector`              | `{}`                     | Pin vLLM to GPU node                   |
| `vllm.tolerations`               | `[]`                     | GPU node taints                        |
| `demoApp.langfuse.publicKey`     | `pk-lf-change-me`        | From Langfuse project settings         |
| `demoApp.langfuse.secretKey`     | `sk-lf-change-me`        | From Langfuse project settings         |
| `tenants.*.apiKey`               | `gw-*-change-me`         | Must match Gravitee API keys           |
| `grafana.adminPassword`          | `demo-grafana-admin`     | Change for any non-local deployment    |
| `gravitee.enabled`               | `true`                   | Disable to skip Layer 1 entirely       |
| `orchestrator.enabled`           | `false`                  | Run load generator as on-cluster Job   |
| `orchestrator.tenantKeys.*`      | `""`                     | Gravitee keys from the bootstrap Job — set in `values-override.yaml` |
| `langfuse.langfuse.nextauth.url` | `http://localhost:3001`  | URL you open Langfuse at — set in `values-override.yaml` |
| `netbird.enabled`                | `false`                  | NetBird NetworkResources — set in `values-override.yaml` |
| `orchestrator.args`              | `[]`                     | e.g. `["--phase","slow"]`, `["--dry-run"]` |
| `orchestrator.burstRps`          | `"30"`                   | Burst RPS for slow phase               |
| `natsWorker.concurrency`         | `32`                     | Concurrent LiteLLM requests per worker |
| `ingress.enabled`                | `false`                  | Enable for conference cluster access   |

---

## Cleanup

```bash
helm uninstall ai-obs-demo -n ai-obs-demo
kubectl delete namespace ai-obs-demo
```
