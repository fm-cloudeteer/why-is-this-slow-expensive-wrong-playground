# Why Is This Slow, Expensive, and Wrong?

## Observability Across the AI Stack — Conference Demo

This repo contains everything needed to run the live demo: a Helm chart that deploys
the full observability stack on Kubernetes, and a Python orchestrator that drives the
three failure scenarios (slow, expensive, wrong).

---

## End-to-end setup guide

### 1. Prerequisites

- A Kubernetes cluster (GPU node for full demo, or a local cluster for rehearsal)
- Helm 3.12+
- Python 3.10+
- `kubectl` configured for your cluster

For **local rehearsal** on a laptop (CPU-only, no GPU required):
- Docker Desktop, OrbStack, or Rancher Desktop with Kubernetes enabled
- At least 16 GB RAM allocated to the Kubernetes VM

---

### 2. GPU setup (skip for CPU-only rehearsal)

If your cluster has NVIDIA GPUs, the Kubernetes node needs the **NVIDIA device plugin**
to advertise GPU resources. NVIDIA drivers must already be installed on the host.

```bash
helm repo add nvdp https://nvidia.github.io/k8s-device-plugin
helm repo update
helm install nvdp nvdp/nvidia-device-plugin \
  --namespace kube-system \
  --set runtimeClassName=nvidia \
  --set-json 'affinity={}'
```

Verify GPUs are visible (may take a few seconds after install):

```bash
kubectl get nodes -o json | jq '.items[].status.capacity | with_entries(select(.key | contains("nvidia")))'
# Expected: {"nvidia.com/gpu": "N"} where N = number of GPUs
```

> **Note:** The `affinity={}` override disables the default Node Feature Discovery
> label requirement. If you have NFD installed, you can omit this flag.

---

### 3. Deploy the stack with Helm

```bash
cd demo-helm

# Add all required Helm repos
helm repo add grafana    https://grafana.github.io/helm-charts
helm repo add prometheus https://prometheus-community.github.io/helm-charts
helm repo add nats       https://nats-io.github.io/k8s/helm/charts
helm repo add langfuse   https://langfuse.github.io/langfuse-k8s
helm repo add bitnami    https://charts.bitnami.com/bitnami
helm repo add elastic    https://helm.elastic.co
helm repo add gravitee   https://helm.gravitee.io
helm repo update

# Fetch sub-chart dependencies
helm dependency update
```

**GPU cluster install:**
```bash
helm install ai-obs-demo . -f values.yaml \
  --namespace ai-obs-demo --create-namespace \
  --set vllm.hfToken=hf_yourtoken \
  --set demoApp.langfuse.publicKey=pk-lf-yourkey \
  --set demoApp.langfuse.secretKey=sk-lf-yourkey
```

**CPU-only laptop rehearsal:**
```bash
helm install ai-obs-demo . -f values.yaml -f values-local.yaml \
  --namespace ai-obs-demo --create-namespace
```

---

### 4. Wait for the stack to become ready

vLLM model loading takes 2-5 minutes. Wait for it before proceeding:

```bash
kubectl rollout status deployment/ai-obs-demo-vllm -n ai-obs-demo
```

If Gravitee is enabled, also watch the bootstrap Job that provisions API keys:

```bash
kubectl logs -l job-name=ai-obs-demo-gravitee-bootstrap -n ai-obs-demo -f
```

Check all pods are running:

```bash
kubectl get pods -n ai-obs-demo
```

---

### 5. Port-forward all services

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

Verify access:
- Grafana: http://localhost:3000 (admin / `demo-grafana-admin`)
- Langfuse: http://localhost:3001
- Prometheus: http://localhost:9090
- Gravitee Console: http://localhost:8002 (admin / admin)

---

### 6. Set up the Python orchestrator

```bash
cd demo-script

pip install -r requirements.txt
pip install fastapi uvicorn httpx langfuse

cp .env.example .env
```

Edit `.env` and set these **required** variables:

```
TENANT_A_API_KEY=<from values.yaml or Gravitee bootstrap output>
TENANT_B_API_KEY=<from values.yaml or Gravitee bootstrap output>
TENANT_C_API_KEY=<from values.yaml or Gravitee bootstrap output>
TENANT_D_API_KEY=<from values.yaml or Gravitee bootstrap output>
LANGFUSE_PUBLIC_KEY=<from Langfuse project settings>
LANGFUSE_SECRET_KEY=<from Langfuse project settings>
FEATURE_FLAG_URL=http://localhost:8080
```

**Note:** `FEATURE_FLAG_URL` must point to the demo app on port 8080 (the feature flag
service is built into the demo app).

Load the env vars:

```bash
export $(cat .env | xargs)
```

---

### 7. Start the demo app

```bash
uvicorn demo_app.main:app --host 0.0.0.0 --port 8080
```

---

### 8. Validate the setup

Run a dry-run to check config and stack health without sending any load:

```bash
python orchestrator.py --dry-run
```

This verifies that all endpoints are reachable and API keys are valid.

---

### 9. Run the demo

**Full 15-minute run (all phases):**
```bash
python orchestrator.py
```

**Single phase (wraps in baseline + recovery automatically):**
```bash
python orchestrator.py --phase slow
python orchestrator.py --phase expensive
python orchestrator.py --phase wrong
```

**On-cluster (eliminates port-forward overhead):**
```bash
# Build image on cluster (see AGENTS.md for scp + docker build steps)
kubectl delete job ai-obs-demo-orchestrator -n ai-obs-demo 2>/dev/null
helm upgrade ai-obs-demo ./demo-helm -f demo-helm/values.yaml \
  --namespace ai-obs-demo --no-hooks \
  --set orchestrator.enabled=true
kubectl logs -f job/ai-obs-demo-orchestrator -n ai-obs-demo
```

The demo runs through these phases:

| Phase     | Duration | What happens                                        |
|-----------|----------|-----------------------------------------------------|
| baseline  | 3 min    | Clean load, all tenants healthy                     |
| wrong     | 4 min    | `prompt_regression_active` flag ON, tenant_b degrades |
| expensive | 3 min    | tenant_c gets ~1500-token padded prompts            |
| slow      | 3 min    | 30 RPS burst (90s) + drain (90s)                    |
| recovery  | 2 min    | Return to baseline                                  |

---

### 10. After the run — prepare the talk

**Find the hero trace:**
```bash
python find_hero_trace.py
```

This reads `run_manifest.json`, queries Tempo for the burst window, and prints a
Grafana deep-link for the trace with the longest queue wait span.

**Set Grafana time ranges** using the timestamps printed at the end of the run.

**Verify all five pre-staged browser tabs:**

| Tab | Tool    | What to check                                     |
|-----|---------|----------------------------------------------------|
| 1   | Grafana | Three panels show baseline, anomalies, recovery    |
| 2   | Tempo   | Trace search loaded, burst window filtered          |
| 3   | Tempo   | Hero trace open, all spans visible and expanded     |
| 4   | Langfuse| Cost view, tenant_c clearly outlier                 |
| 5   | Langfuse| Quality trend, tenant_b step-change visible         |

---

## Architecture

```
                    Gravitee APIM (Layer 1)
                         |
                    LiteLLM proxy
                         |
                    NATS JetStream (Layer 2)
                         |
                       vLLM
                         |
                    Demo App (Layer 3)
```

| Layer | Component | Failure demonstrated |
|---|---|---|
| 1 | Gravitee APIM | API key enforcement, traceparent injection |
| 2 | LiteLLM + NATS + vLLM | Latency (queue backup), cost (token volume) |
| 3 | Demo app | Quality (wrong system prompt via feature flag) |

Alloy scrapes all metrics endpoints every 15s, forwards OTLP traces to Tempo
and logs to Loki. Grafana datasources (Prometheus, Loki, Tempo) are pre-wired;
dashboards are served from the `ai-obs-demo-dashboards` ConfigMap.

---

## Cleanup

```bash
helm uninstall ai-obs-demo -n ai-obs-demo
kubectl delete namespace ai-obs-demo
```

---

## Repo layout

```
demo-helm/      Helm 3 chart — deploys full observability stack on Kubernetes
demo-script/    Python orchestrator + FastAPI demo app
AGENTS.md       Operational reference for AI coding agents
```

See `demo-helm/README.md` and `demo-script/README.md` for component-specific details.
