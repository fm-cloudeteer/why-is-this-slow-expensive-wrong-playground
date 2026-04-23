# Why Is This Slow, Expensive, and Wrong?

## Observability Across the AI Stack

This repo contains the full playground from the [Leaseweb Tech Meetup](https://luma.com/klg1zbe2) talk with the same name. It deploys a
multi-tenant LLM inference stack with end-to-end observability on Kubernetes,
then runs scripted failure scenarios so you can explore how each layer of
observability catches different problems.


**Three failure patterns demonstrated:**

| Pattern | What breaks | What catches it |
|---------|-------------|-----------------|
| **Slow** | NATS queue backs up under burst load | Prometheus metrics, Tempo traces (queue wait spans) |
| **Expensive** | One tenant sends bloated prompts (10x tokens) | Langfuse cost attribution, Prometheus token counters |
| **Wrong** | Bad system prompt deployed via feature flag | Langfuse quality scores (infra metrics stay green) |

---

## Architecture

```
Orchestrator (load generator)
       |
  Gravitee APIM          ← Layer 1: API gateway, tenant key enforcement, trace injection
       |
  Demo App (FastAPI)      ← Layer 3: system prompt injection, quality scoring, feature flags
       |
  NATS JetStream          ← Layer 2: inference queue (visible queue depth under load)
       |
  NATS Worker             ← pulls from queue, forwards to LiteLLM
       |
  LiteLLM                 ← model router, cost tracking → Langfuse
       |
  vLLM                    ← LLM inference runtime (GPU)
```

**Observability stack:** Grafana + Prometheus + Tempo + Loki + Alloy (OTel collector) + Langfuse

---

## Quick start

### 1. Set up a k3s cluster

[k3s](https://k3s.io/) is the easiest way to get a single-node Kubernetes cluster running.

```bash
# Install k3s
curl -sfL https://get.k3s.io | sh -

# Verify
sudo k3s kubectl get nodes
```

Configure `kubectl` to use the k3s kubeconfig:

```bash
mkdir -p ~/.kube
sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
sudo chown $USER ~/.kube/config
kubectl get nodes
```

> **Note:** Any Kubernetes 1.26+ cluster works (k3s, k0s, kind, EKS, GKE, etc.).
> k3s is recommended for single-node GPU setups because it ships with the `nvidia`
> RuntimeClass pre-configured.

### 2. GPU setup (requires NVIDIA drivers on the host)

The host must have [NVIDIA drivers](https://docs.nvidia.com/datacenter/tesla/driver-installation-guide/)
installed. Then install the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
and the [NVIDIA Device Plugin](https://github.com/NVIDIA/k8s-device-plugin) for Kubernetes:

```bash
# Install NVIDIA Container Toolkit (makes GPUs visible to containers)
# See: https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html

# Install the Kubernetes device plugin (makes GPUs schedulable)
helm repo add nvdp https://nvidia.github.io/k8s-device-plugin
helm repo update
helm install nvdp nvdp/nvidia-device-plugin \
  --namespace kube-system \
  --set runtimeClassName=nvidia \
  --set-json 'affinity={}'
```

Verify GPUs are visible:

```bash
kubectl get nodes -o json | \
  jq '.items[].status.capacity | with_entries(select(.key | contains("nvidia")))'
# Expected: {"nvidia.com/gpu": "N"}
```

> The `affinity={}` override disables the default
> [Node Feature Discovery](https://github.com/kubernetes-sigs/node-feature-discovery)
> label requirement. If you have NFD installed, you can omit this flag.

### 3. Deploy the stack

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

# Install
helm install ai-obs-demo . -f values.yaml \
  --namespace ai-obs-demo --create-namespace
```

> **Model:** The default model is `cyankiwi/Qwen3.5-9B-AWQ-4bit` (4-bit quantized,
> fits on a single RTX 3090/4090 with 24 GB VRAM). It's a public model — no
> HuggingFace token needed. Change `vllm.model` in `values.yaml` for a different model.

### 4. Wait for readiness

vLLM model loading takes 2-5 minutes:

```bash
kubectl rollout status deployment/ai-obs-demo-vllm -n ai-obs-demo
```

Watch the Gravitee bootstrap Job (provisions API keys for all tenants):

```bash
kubectl logs -l job-name=ai-obs-demo-gravitee-bootstrap -n ai-obs-demo -f
```

Check all pods:

```bash
kubectl get pods -n ai-obs-demo
```

### 5. Access the services

```bash
kubectl port-forward svc/ai-obs-demo-grafana           3000:80   -n ai-obs-demo &
kubectl port-forward svc/ai-obs-demo-langfuse-web      3001:3000 -n ai-obs-demo &
kubectl port-forward svc/ai-obs-demo-prometheus-server 9090:80   -n ai-obs-demo &
kubectl port-forward svc/ai-obs-demo-tempo             3200:3100 -n ai-obs-demo &
kubectl port-forward svc/ai-obs-demo-app               8080:8080 -n ai-obs-demo &
kubectl port-forward svc/ai-obs-demo-litellm           4000:4000 -n ai-obs-demo &
kubectl port-forward svc/ai-obs-demo-gravitee-gateway  8082:8082 -n ai-obs-demo &
kubectl port-forward svc/ai-obs-demo-gravitee-ui       8002:8002 -n ai-obs-demo &
```

| Service | URL | Credentials |
|---------|-----|-------------|
| [Grafana](https://grafana.com/grafana/) | http://localhost:3000 | admin / `demo-grafana-admin` |
| [Langfuse](https://langfuse.com/) | http://localhost:3001 | demo@demo.com / `demo-admin-password` |
| [Prometheus](https://prometheus.io/) | http://localhost:9090 | — |
| [Gravitee Console](https://www.gravitee.io/) | http://localhost:8002 | admin / admin |

---

## Running the demo

### Option A: On-cluster (recommended)

The orchestrator runs as a Kubernetes Job — no port-forwarding or local Python needed.

```bash
# Build the orchestrator image on the cluster
# (see AGENTS.md for the full scp + docker build commands)

# Run all 5 phases (~15 min)
kubectl delete job ai-obs-demo-orchestrator -n ai-obs-demo 2>/dev/null
helm upgrade ai-obs-demo . -f values.yaml \
  --namespace ai-obs-demo --no-hooks \
  --set orchestrator.enabled=true

# Watch logs
kubectl logs -f job/ai-obs-demo-orchestrator -n ai-obs-demo

# Run a single phase
helm upgrade ai-obs-demo . -f values.yaml \
  --namespace ai-obs-demo --no-hooks \
  --set orchestrator.enabled=true \
  --set 'orchestrator.args={--phase,slow}'

# Disable after run
helm upgrade ai-obs-demo . -f values.yaml \
  --namespace ai-obs-demo --no-hooks \
  --set orchestrator.enabled=false
```

### Option B: Local Python

```bash
cd demo-script
pip install -r requirements.txt
cp .env.example .env
# Edit .env — set tenant API keys and Langfuse credentials
```

```bash
# Validate config
python orchestrator.py --dry-run

# Full 15-minute run
python orchestrator.py

# Single phase
python orchestrator.py --phase slow
python orchestrator.py --phase expensive
python orchestrator.py --phase wrong
```

### Demo phases

| Phase | Duration | What happens |
|-------|----------|--------------|
| baseline | 3 min | Clean load at 4 RPS (1 per tenant), all healthy |
| wrong | 4 min | Feature flag injects bad system prompt for tenant_b |
| expensive | 3 min | tenant_c receives ~1500-token padded prompts |
| slow | 3 min | 30 RPS burst (90s) + drain (90s) |
| recovery | 2 min | Return to baseline |

### After the run

```bash
# Find the hero trace (longest queue wait during burst)
python find_hero_trace.py
```

The orchestrator prints Grafana time range timestamps at the end of every run.
Open the pre-provisioned dashboard and set the time range to match.

---

## What to explore

### Grafana dashboard

The pre-provisioned dashboard ("AI Obs Demo") shows:

- **Latency panels** — p99/p50 request latency, vLLM time-to-first-token
- **Queue depth** — NATS JetStream pending messages (spikes during burst)
- **Token spend** — stacked bar chart of input/output tokens per tenant
- **Request rate** — per-tenant req/s and error rate
- **GPU utilization** — DCGM metrics (util %, VRAM %)
- **Quality scores** — Langfuse quality scores per tenant

### Tempo (distributed tracing)

Each request produces a trace spanning: Gravitee → Demo App → NATS → Worker → LiteLLM → vLLM.
During the burst phase, the `nats-worker-process` span shows `queue_wait_ms` — the time
the request spent waiting in the NATS queue before a worker picked it up.

### Langfuse

- **Cost view** — tenant_c's cost diverges during the expensive phase
- **Quality scores** — tenant_b's quality drops when the bad system prompt is injected
- **Traces** — full LLM call details with token usage, latency, and model parameters

---

## Project links

| Component | Project | Role in this demo |
|-----------|---------|-------------------|
| [k3s](https://k3s.io/) | Lightweight Kubernetes | Single-node cluster runtime |
| [vLLM](https://github.com/vllm-project/vllm) | LLM inference engine | Serves the quantized Qwen model on GPU |
| [LiteLLM](https://github.com/BerriAI/litellm) | LLM proxy / router | Cost tracking, Langfuse callback, OpenAI-compatible API |
| [NATS](https://nats.io/) | Message queue (JetStream) | Inference request queue — creates visible backpressure |
| [Gravitee APIM](https://www.gravitee.io/) | API gateway | Tenant API key enforcement, W3C traceparent injection |
| [Langfuse](https://langfuse.com/) | LLM observability | Cost attribution, quality scoring, LLM trace storage |
| [Grafana](https://grafana.com/grafana/) | Dashboards | Unified view of metrics, traces, and logs |
| [Prometheus](https://prometheus.io/) | Metrics storage | Scrapes all `/metrics` endpoints via Alloy |
| [Tempo](https://grafana.com/oss/tempo/) | Distributed trace storage | Stores OTLP traces from all services |
| [Loki](https://grafana.com/oss/loki/) | Log aggregation | Centralized logs from all pods |
| [Alloy](https://grafana.com/docs/alloy/) | OpenTelemetry collector | Scrapes metrics, receives OTLP traces, forwards to backends |
| [NVIDIA DCGM](https://developer.nvidia.com/dcgm) | GPU metrics exporter | GPU utilization and VRAM usage for Prometheus |
| [Helm](https://helm.sh/) | Kubernetes package manager | Deploys the entire stack as a single chart |

---

## Repo layout

```
demo-helm/           Helm 3 chart — deploys the full stack on Kubernetes
  templates/         Custom templates (vLLM, demo app, LiteLLM, NATS worker, etc.)
  values.yaml        All configuration (model, replicas, credentials, tuning)

demo-script/         Python orchestrator + FastAPI demo app
  orchestrator.py    Main entry point — runs all 5 phases
  demo_app/          FastAPI app (Layer 3 — prompt injection, scoring, flags)
  phases/            Phase implementations (baseline, wrong, expensive, slow, recovery)
  prompts/           Prompt generators (normal ~80 tokens, expensive ~1500 tokens)
  utils/             Load generator, health checks, feature flag client
  find_hero_trace.py Post-run: finds best queue-wait trace in Tempo

AGENTS.md            Operational reference (pitfalls, debugging, architecture details)
```

---

## Cleanup

```bash
helm uninstall ai-obs-demo -n ai-obs-demo
kubectl delete namespace ai-obs-demo
```

---

## License

This is a conference demo — use it however you like at your own risk.
