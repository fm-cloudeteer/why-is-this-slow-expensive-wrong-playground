# Demo script — "Why Is This Slow, Expensive, and Wrong?"

Orchestrates all three failure patterns across a 15-minute run window,
then helps you find the hero trace for the Tab 3 demo moment.

---

## Repository structure

```
orchestrator.py          Main entry point — runs all phases in sequence
find_hero_trace.py       Post-run: finds best queue-wait trace in Tempo
config.py                DemoConfig dataclass, loaded from env
.env.example             Copy to .env and fill in your values

phases/
  baseline.py            Phase 1 — 3 min clean baseline
  wrong.py               Phase 2 — 4 min prompt regression (tenant_b)
  expensive.py           Phase 3 — 3 min token-heavy prompts (tenant_c)
  slow.py                Phase 4 — 3 min burst load + drain
  recovery.py            Phase 5 — 2 min return to baseline

prompts/
  normal.py              Normal-length customer support queries (~80 tokens)
  expensive.py           Padded prompts with fake history (~1,500 tokens)

utils/
  load_generator.py      Async HTTP load generator, per-tenant RPS control
  feature_flags.py       HTTP client for the demo app flag endpoint
  health.py              Stack health checker, runs before every phase
  logger.py              Coloured terminal output with timestamps
  manifest.py            Writes run_manifest.json for Grafana pre-staging

demo_app/
  main.py                FastAPI service — Layer 3 demo app
  flags.py               In-memory feature flag store + HTTP endpoints
  prompts.py             System prompt selector (good vs degraded)
  scorer.py              Rule-based quality scorer (deterministic)
```

---

## Prerequisites

```bash
pip install -r requirements.txt

# Demo app additional dependencies
pip install fastapi uvicorn httpx langfuse
```

---

## Setup

```bash
cp .env.example .env
# Edit .env with your stack endpoints and API keys
export $(cat .env | xargs)
```

---

## Running the demo app

The demo app must be running and reachable at `FEATURE_FLAG_URL`
before you start the orchestrator.

```bash
uvicorn demo_app.main:app --host 0.0.0.0 --port 8080
```

The demo app exposes:
- `POST /v1/chat/completions` — proxies to LiteLLM with system prompt injection
- `GET  /flags`               — list all feature flags
- `POST /flags/{name}`        — set a flag (used by orchestrator)
- `GET  /health`              — health check

---

## Running the orchestrator

### Full run (recommended — ~15 minutes)
```bash
python orchestrator.py
```

### Validate config only (no load)
```bash
python orchestrator.py --dry-run
```

### Single phase (wraps in baseline + recovery)
```bash
python orchestrator.py --phase slow
python orchestrator.py --phase expensive
python orchestrator.py --phase wrong
```

---

## After the run

### 1. Find the hero trace
```bash
python find_hero_trace.py
```
Reads `run_manifest.json`, queries Tempo for the burst window,
and prints the Grafana deep-link for the trace with the longest
queue wait span. Bookmark this URL — it's your demo Tab 3.

### 2. Set Grafana time ranges
The orchestrator prints exact timestamps at the end of every run:
```
From: 2024-12-03T14:08:00Z
To:   2024-12-03T14:23:00Z
```
Set all five Grafana/Langfuse tabs to this window before the talk.

### 3. Verify the five pre-staged tabs
| Tab | Tool      | What to check                                      |
|-----|-----------|----------------------------------------------------|
| 1   | Grafana   | Three panels show baseline → anomalies → recovery  |
| 2   | Tempo     | Trace search loaded, burst window filtered          |
| 3   | Tempo     | Hero trace open, all spans visible and expanded     |
| 4   | Langfuse  | Cost view, tenant_c clearly outlier                 |
| 5   | Langfuse  | Quality trend, tenant_b step-change visible         |

---

## Tuning BURST_RPS

The burst needs to be dramatic enough to fill the queue but not
crash vLLM. Find your sustainable throughput first:

```bash
python orchestrator.py --phase slow  # watch the logs
# Look for: mean latency < 1000ms at baseline, p99 > 5000ms at burst peak
```

If the queue barely moves, increase `BURST_RPS`.
If vLLM logs show OOM errors, decrease it.
Target: queue depth reaches 20-40 messages at peak, p99 hits 5-9s.

---

## Reproducibility

All random seeds are fixed:
- `prompts/normal.py`    — `random.Random(42)`
- `prompts/expensive.py` — `random.Random(99)`

Every rehearsal run produces the same prompt sequence, token counts,
and quality scores. The only variance is network timing.
