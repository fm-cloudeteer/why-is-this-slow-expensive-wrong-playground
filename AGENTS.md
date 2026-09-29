# AGENTS.md

Conference demo repo for "Why Is This Slow, Expensive, and Wrong? Observability Across the AI Stack."  
No CI, no test suite, no linter. The only validation tool is `--dry-run`.

---

## Repo layout

```
demo-helm/      Helm 3 chart — deploys full observability stack on Kubernetes
demo-script/    Python orchestrator + FastAPI demo app
```

---

## demo-helm — Helm chart

### Required setup before first install

```bash
helm repo add grafana    https://grafana.github.io/helm-charts
helm repo add prometheus https://prometheus-community.github.io/helm-charts
helm repo add nats       https://nats-io.github.io/k8s/helm/charts
helm repo add langfuse   https://langfuse.github.io/langfuse-k8s
helm repo add bitnami    https://charts.bitnami.com/bitnami
helm repo add elastic    https://helm.elastic.co
helm repo add gravitee   https://helm.gravitee.io
helm repo update
helm dependency update   # must run before install; no Chart.lock is committed
```

### Install commands

```bash
# Cluster-specific overrides (git-ignored): cp values-override.example.yaml values-override.yaml

# GPU cluster
helm install ai-obs-demo . -f values.yaml -f values-override.yaml \
  --namespace ai-obs-demo --create-namespace \
  --set vllm.hfToken=hf_yourtoken \
  --set demoApp.langfuse.publicKey=pk-lf-yourkey \
  --set demoApp.langfuse.secretKey=sk-lf-yourkey

# CPU-only laptop rehearsal (uses facebook/opt-125m, disables DCGM)
helm install ai-obs-demo . -f values.yaml -f values-local.yaml -f values-override.yaml \
  --namespace ai-obs-demo --create-namespace

helm uninstall ai-obs-demo -n ai-obs-demo
kubectl delete namespace ai-obs-demo
```

### Port-forward all services

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

### Non-obvious Helm facts

- **GPU prerequisite**: NVIDIA drivers must be installed on the host. The NVIDIA device plugin DaemonSet must be deployed separately (`nvdp/nvidia-device-plugin` chart) with `runtimeClassName=nvidia` and `affinity={}` (to skip NFD label requirement). Without it, `nvidia.com/gpu` resources are not advertised to the kubelet.
- The vLLM pod spec sets `runtimeClassName: nvidia` when `vllm.gpu.enabled` is true — required on k3s where the NVIDIA runtime is not the default.
- **Cluster-specific values** live in `values-override.yaml` (git-ignored; template:
  `values-override.example.yaml`): Langfuse `nextauth.url`, NetBird router/groups, orchestrator
  tenant keys. Always pass it after `values.yaml` — a plain `-f values.yaml` upgrade disables NetBird
  and resets the Langfuse login redirect.
- `helm dependency update` resolves to latest-compatible; no lock file is committed. Versions in `Chart.yaml` are pinned strings, so this is stable.
- `vllm.hfToken` (inline) vs `vllm.hfTokenSecret` (existing Secret name) — both paths supported; inline not recommended for shared deployments.
- `demoApp.langfuseExistingSecret` bypasses the chart-managed Secret — controlled via the `ai-obs-demo.langfuseSecretName` helper in `_helpers.tpl`.
- LiteLLM master key defaults to `sk-demo-master-key-change-me` — change before any non-local deployment.
- Langfuse chart `1.5.27` (v3): `salt` and `nextauth.secret` must be changed before conference/shared deployment.
- Gravitee APIM is now in the chart (see section below). It was previously external.
- Bulk cleanup: `kubectl delete all -l app.kubernetes.io/part-of=ai-obs-demo`
- vLLM model loading takes 2–5 min: `kubectl rollout status deployment/ai-obs-demo-vllm -n ai-obs-demo`
- **Model cache**: `vllm.modelCache.type` controls the HF cache volume — `emptyDir` (default, re-downloads every restart), `hostPath` (pre-downloaded on node), or `pvc`. For GPU clusters, use `hostPath` with the model pre-downloaded to avoid multi-minute cold starts.
- **Demo app image**: Must be built on the cluster (or for `linux/amd64`) and imported into k3s containerd. Building on Apple Silicon produces `arm64` images that k3s silently ignores. Build on cluster:
  ```bash
  scp -r demo-script/Dockerfile demo-script/demo_app meyerfel@ai.meyer3d.de:/tmp/demo-app-build/
  ssh meyerfel@ai.meyer3d.de 'cd /tmp/demo-app-build && sudo k3s ctr images rm docker.io/library/ai-obs-demo-app:latest; sudo docker build -t ai-obs-demo-app:latest . && sudo docker save ai-obs-demo-app:latest | sudo k3s ctr images import -'
  kubectl rollout restart deployment/ai-obs-demo-app -n ai-obs-demo
  ```
- **k3s containerd image import**: `k3s ctr images import` does not overwrite existing tags. Must `k3s ctr images rm` first, then import.
- **Demo app → LiteLLM auth**: The demo app must pass `Authorization: Bearer <master_key>` to LiteLLM. Controlled by `LITELLM_KEY` env var (defaults to `sk-demo-master-key-change-me`).

### NATS JetStream inference queue

The demo app publishes inference requests to a NATS JetStream stream (`inference`)
using the request-reply pattern. A separate **NATS worker** pod pulls messages,
calls LiteLLM, and replies. This creates a visible queue during burst load.

**Request flow**: Orchestrator → Gravitee → Demo App → NATS (`inference.request`) → NATS Worker → LiteLLM → vLLM → reply back via NATS → Demo App.

**Stream config**: `inference` stream, `WorkQueuePolicy` retention, memory storage, 2 min max age.

**Worker concurrency**: Controlled by `natsWorker.concurrency` in `values.yaml` (default 32).
At baseline 4 RPS, 32 concurrent slots handle the load. During the burst (`orchestrator.burstRps`,
default 8), the queue backs up because only ~8.6 req/s can drain (32 slots / ~3.7 s per request)
— creating the "slow" demo pattern.

**NATS worker image**: Separate image (`ai-obs-demo-nats-worker:latest`), built from
`demo-script/nats_worker/Dockerfile`. Must be built on the cluster (same as demo app):
```bash
scp -r demo-script/nats_worker meyerfel@ai.meyer3d.de:/tmp/nats-worker-build/
ssh meyerfel@ai.meyer3d.de 'cd /tmp/nats-worker-build && sudo k3s ctr images rm docker.io/library/ai-obs-demo-nats-worker:latest; sudo docker build -t ai-obs-demo-nats-worker:latest -f Dockerfile . && sudo docker save ai-obs-demo-nats-worker:latest | sudo k3s ctr images import -'
kubectl rollout restart deployment/ai-obs-demo-nats-worker -n ai-obs-demo
```

**Prometheus metrics exposed**:
- `nats_queue_wait_ms_bucket` (histogram) — emitted by the NATS worker on `:9100/metrics`
- `nats_consumer_num_pending{stream='inference'}` — emitted by the `prometheus-nats-exporter`
  sidecar (built into the NATS Helm chart, enabled via `nats.promExporter.enabled: true`)

**OTEL span attribute**: The worker sets `queue_wait_ms` on each `nats-worker-process` span.
This is what `find_hero_trace.py` searches for when finding the hero trace for Grafana Tab 3.

**NATS exporter**: Runs as a sidecar in the NATS pod (not a separate deployment).
Enabled via `nats.promExporter.enabled: true`. The chart passes `-jsz=all -varz -connz`
automatically. Alloy scrapes it at `ai-obs-demo-nats-0.ai-obs-demo-nats-headless:7777`.

### Pre-downloading the model (hostPath cache)

```bash
ssh meyerfel@ai.meyer3d.de
sudo mkdir -p /opt/models/huggingface
sudo chown $USER /opt/models/huggingface
pip install huggingface-hub   # if not already installed
huggingface-cli download Qwen/Qwen3.5-9B --cache-dir /opt/models/huggingface
```

The vLLM container mounts `/opt/models/huggingface` at `/root/.cache/huggingface`, so the standard HF cache layout is used directly.

### Langfuse bootstrap Job

**DEPRECATED** — gated on `langfuseBootstrap.enabled: false`. Was used for Langfuse v2;
Langfuse v3 uses headless initialization via `LANGFUSE_INIT_*` env vars instead (see below).

### Langfuse v3 (chart 1.5.27)

Langfuse v3 requires PostgreSQL + ClickHouse + Redis + S3/MinIO (all deployed as subcharts).

**Persistence is required**: PostgreSQL and ClickHouse must have `persistence.enabled: true`
in `values.yaml`. With `enabled: false` (the Bitnami default), data lives on `emptyDir` and
is **wiped on every pod restart** — including all Prisma migration state and Langfuse data.
Changing persistence on an existing StatefulSet requires deleting and recreating it:
```bash
kubectl delete statefulset ai-obs-demo-langfuse-postgresql -n ai-obs-demo --cascade=orphan
kubectl delete pod ai-obs-demo-langfuse-postgresql-0 -n ai-obs-demo
helm upgrade ...  # recreates with PVC
kubectl rollout restart deployment/ai-obs-demo-langfuse-web -n ai-obs-demo  # re-runs migrations
```

**Headless initialization**: Org, project, user, and API keys are created on startup via
`LANGFUSE_INIT_*` env vars set in `langfuse.langfuse.additionalEnv` in `values.yaml`.
No bootstrap Job needed.

**Subchart service naming pitfall**: The Langfuse chart templates expect services named
`{{ .Release.Name }}-langfuse-<subchart>` (e.g. `ai-obs-demo-langfuse-postgresql`), but
Bitnami subcharts generate names without the `langfuse-` prefix by default. **Fix**: set
`fullnameOverride` on each subchart:
```yaml
langfuse:
  postgresql:
    fullnameOverride: "ai-obs-demo-langfuse-postgresql"
  clickhouse:
    fullnameOverride: "ai-obs-demo-langfuse-clickhouse"
    zookeeper:
      fullnameOverride: "ai-obs-demo-langfuse-zookeeper"
  redis:
    fullnameOverride: "ai-obs-demo-langfuse-redis"
  s3:
    fullnameOverride: "ai-obs-demo-langfuse-s3"
```
Also set `postgresql.host` to match the fullnameOverride value.

**Langfuse service name**: `ai-obs-demo-langfuse-web` (not `ai-obs-demo-langfuse`).
Port-forward command:
```bash
kubectl port-forward svc/ai-obs-demo-langfuse-web 3001:3000 -n ai-obs-demo
```

**Langfuse Python SDK v4 API** (langfuse >= 4.0):
- Constructor: `Langfuse(base_url=...)` not `host=...`
- No `.trace()` / `.generation()` — use `langfuse.start_observation(name=..., as_type="span")`
- Nested observations: `span.start_observation(name=..., as_type="generation")`
- Update: `span.update(output=..., usage_details=..., metadata=...)`
- End: `span.end()`
- Score: `span.score_trace(name=..., value=...)` or `langfuse.create_score(...)`
- No `update_trace()` — use `span.set_trace_io(input=..., output=...)` for trace I/O
- Token usage: `usage_details={"input": N, "output": N, "total": N}` (not `usage`)

### Gravitee APIM (Layer 1)

Gravitee is a sub-chart dependency (`gravitee/apim` 4.11.3, alias `gravitee`). MongoDB
and Elasticsearch are bundled as subcharts of the apim chart (not separate Chart.yaml
dependencies). All are gated on `gravitee.enabled`.

**Post-install bootstrap Job** (`templates/gravitee-bootstrap.yaml`):
- Runs automatically after `helm install` / `helm upgrade`
- Waits for the Management API, creates the demo API + API Key plan, creates one
  application per tenant, subscribes each; API keys are auto-generated by Gravitee
  (custom key values cannot be set via the API) — copy them from the Job logs into `.env`
- `backoffLimit: 5`, `activeDeadlineSeconds: 600` — tolerates slow Management API cold start
- If the custom key PUT returns non-200, the Job logs the generated key and warns; update
  `TENANT_*_API_KEY` in `.env` to match before running the orchestrator
- Re-trigger by running `helm upgrade` if the Job fails permanently

Watch bootstrap progress:
```bash
kubectl logs -l job-name=ai-obs-demo-gravitee-bootstrap -n ai-obs-demo -f
```

**Gravitee default admin credentials:** `admin` / `admin` — change before any shared deployment.

**W3C `traceparent` propagation**: handled by Gravitee's built-in OpenTelemetry support.
Spans are forwarded to Alloy on port 4317 (gRPC) and appear in Tempo.

**Gravitee OTEL config** (APIM 4.6+, native `services.opentelemetry` in `gravitee.yml`):
```yaml
gateway:
  services:
    opentelemetry:
      enabled: true
      exporter:
        endpoint: http://ai-obs-demo-alloy:4317
        protocol: grpc
```
**Note**: APIM 4.0–4.5 used `gravitee_services_tracing_*` env vars instead. APIM 4.6+
uses the native `services.opentelemetry` config path shown above.

**Bundled ES is security-disabled** (`xpack.security.enabled: false`) — intentional for demo.

**CRITICAL — v4-emulation-engine endpoint timeout pitfall**: In Gravitee APIM 4.x, when a
v2 API runs under `execution_mode: v4-emulation-engine`, the gateway ignores the
**group-level** `proxy.groups[].http.readTimeout` and instead uses the **endpoint-level**
`proxy.groups[].endpoints[].http.readTimeout`. If the endpoint-level `http` block is not
set, the v4 engine defaults to **10 seconds** — causing HTTP 504 for any LLM inference
request that takes longer. **Fix**: always set `http` config on both the group AND each
endpoint:
```json
{
  "proxy": {
    "groups": [{
      "http": { "readTimeout": 120000, "idleTimeout": 120000 },
      "endpoints": [{
        "http": { "connectTimeout": 10000, "readTimeout": 120000, "idleTimeout": 120000 }
      }]
    }]
  }
}
```
The bootstrap Job (`gravitee-bootstrap.yaml`) does this automatically.

**Gravitee ES reporter**: The bundled Elasticsearch (for analytics/reporting) starts as a
single master-only node with 0 data nodes (cluster status: red). The ES reporter will spam
`Unable to send bulk data` errors every 15s. Disable with
`reporters.elasticsearch.enabled: false` in the gateway config unless you need Gravitee
analytics.

### Orchestrator Job (on-cluster load generator)

The orchestrator can run on-cluster as a Kubernetes Job instead of locally. This eliminates
port-forward overhead and gives more realistic latency measurements.

**Template**: `templates/orchestrator.yaml` — plain Job (no Helm hooks), gated on
`orchestrator.enabled`. Must delete the old Job before re-running (`kubectl delete job`).

**Image**: Built from `demo-script/Dockerfile.orchestrator`. Must be built on the cluster
(same as demo app and NATS worker — Apple Silicon builds won't work on k3s):
```bash
scp -r demo-script/Dockerfile.orchestrator \
       demo-script/orchestrator.py demo-script/config.py \
       demo-script/find_hero_trace.py demo-script/phases \
       demo-script/prompts demo-script/utils \
       meyerfel@ai.meyer3d.de:/tmp/orchestrator-build/
ssh meyerfel@ai.meyer3d.de 'cd /tmp/orchestrator-build && \
  sudo k3s ctr images rm docker.io/library/ai-obs-demo-orchestrator:latest; \
  sudo docker build -t ai-obs-demo-orchestrator:latest -f Dockerfile.orchestrator . && \
  sudo docker save ai-obs-demo-orchestrator:latest | sudo k3s ctr images import -'
```

**Running**:
```bash
# Delete any previous Job
kubectl delete job ai-obs-demo-orchestrator -n ai-obs-demo 2>/dev/null

# Full run (all 5 phases, ~15 min)
helm upgrade ai-obs-demo . -f values.yaml -f values-override.yaml --namespace ai-obs-demo --no-hooks \
  --set orchestrator.enabled=true

# Single phase
helm upgrade ai-obs-demo . -f values.yaml -f values-override.yaml --namespace ai-obs-demo --no-hooks \
  --set orchestrator.enabled=true --set 'orchestrator.args={--phase,slow}'

# Dry-run
helm upgrade ai-obs-demo . -f values.yaml -f values-override.yaml --namespace ai-obs-demo --no-hooks \
  --set orchestrator.enabled=true --set 'orchestrator.args={--dry-run}'

# Watch logs
kubectl logs -f job/ai-obs-demo-orchestrator -n ai-obs-demo

# Disable after run (prevents re-triggering on future upgrades)
helm upgrade ai-obs-demo . -f values.yaml -f values-override.yaml --namespace ai-obs-demo --no-hooks \
  --set orchestrator.enabled=false
```

**Values** (`values.yaml`):
- `orchestrator.enabled`: `false` by default
- `orchestrator.args`: list of CLI args (e.g. `["--phase", "slow"]`)
- `orchestrator.burstRps`: `"8"` (passed as `BURST_RPS` env var)
- `orchestrator.tenantKeys.{a,b,c,d}`: Gravitee API keys for each tenant — set in `values-override.yaml`
  (regenerated by the bootstrap Job on every fresh install)
- `orchestrator.activeDeadlineSeconds`: `1200` (20 min max)

**Env vars**: The template sets all endpoint URLs to in-cluster service names automatically
(Gravitee, Langfuse, Prometheus, Tempo, Demo App). No `.env` file needed inside the container.

---

## demo-script — Python orchestrator + demo app

### Setup

```bash
cd demo-script
pip install -r requirements.txt
pip install fastapi uvicorn httpx langfuse   # demo app deps not in requirements.txt

cp .env.example .env
# Edit .env, then:
export $(cat .env | xargs)
```

### Required env vars (hard error without them)

```
TENANT_A_API_KEY, TENANT_B_API_KEY, TENANT_C_API_KEY, TENANT_D_API_KEY
LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY
```

### `FEATURE_FLAG_URL` mismatch — common mistake

The feature flag service is the demo app itself on port `8080`.  
Always set:
```
FEATURE_FLAG_URL=http://localhost:8080
```

### Key commands

```bash
# Start the FastAPI demo app
uvicorn demo_app.main:app --host 0.0.0.0 --port 8080

# Validate config + stack health without sending load
python orchestrator.py --dry-run

# Full 15-minute demo (all phases in order)
python orchestrator.py

# Run a single failure phase (wraps it in baseline + recovery automatically)
python orchestrator.py --phase slow
python orchestrator.py --phase expensive
python orchestrator.py --phase wrong

# After a run: find the "hero trace" for Grafana Tab 3
python find_hero_trace.py
```

### Demo phase sequence

```
baseline  3 min  clean load, all tenants healthy
wrong     4 min  prompt_regression_active flag ON → tenant_b quality degrades
expensive 3 min  tenant_c receives ~1500-token padded prompts
slow      3 min  BURST_RPS burst (90s) + drain (90s)
recovery  2 min  return to baseline
```

The `prompt_regression_active` flag is **not cleared between phases** — it stays ON through expensive and slow. Only the `flags.reset_all()` call at run start resets it.

### Reproducibility

- `prompts/normal.py` seeds with `random.Random(42)` — same prompt sequence every rehearsal.
- `prompts/expensive.py` seeds with `random.Random(99)`.

### Quality scorer behaviour

Only `tenant_b` gets the full 4-rule quality check. All other tenants score high unless responses are short or refusals.

### run_manifest.json

Written to the `demo-script/` working directory at run end (or partial on Ctrl-C). Records phase timestamps; consumed by `find_hero_trace.py` for Grafana time-range staging.

### Burst tuning

Aim for queue depth 20-40 at peak, p99 5-9 s. The backlog grows by (BURST_RPS − throughput) per second
for the 90 s burst, so `BURST_RPS` must sit only slightly above sustainable throughput
(≈ `natsWorker.concurrency` / per-request latency; ~8.6 req/s with 32 slots and Qwen thinking disabled).
At 30 RPS (≈3.5x) p99 reached ~67 s. Gravitee's backend connection cap is raised to 500 in the bootstrap
Job so the backlog queues in NATS, not invisibly inside the gateway.

The load generator closes its HTTP session at each phase deadline, cancelling in-flight requests.
These show up as errors (HTTP 499 at the gateway, `Stream reset` in gateway logs) right at the burst end.

---

## Architecture layers

| Layer | Component | Failure demonstrated |
|---|---|---|
| 1 | Gravitee APIM | API key enforcement, traceparent injection |
| 2 | LiteLLM + NATS + vLLM | Latency (NATS queue backup), cost (token volume) |
| 3 | Demo app | Quality (wrong system prompt via feature flag) |

Alloy scrapes all metrics endpoints every 15 s and forwards OTLP traces to Tempo and logs to Loki. Grafana datasources (Prometheus, Loki, Tempo) are pre-wired; dashboards are served from the `ai-obs-demo-dashboards` ConfigMap.
