"""
NATS JetStream inference worker.

Pulls inference requests from the "inference" JetStream stream, forwards
them to LiteLLM via HTTP, and replies to the caller via NATS request-reply.

Each processed message gets:
  - An OTEL span with queue_wait_ms attribute (for Tempo / hero trace)
  - A Prometheus histogram observation (for Grafana dashboard)

Concurrency is controlled by WORKER_CONCURRENCY env var. The worker runs
that many concurrent pull-and-process loops, so at most WORKER_CONCURRENCY
requests are in-flight to LiteLLM simultaneously.

Usage:
    python -m nats_worker.worker

Env vars:
    NATS_URL               nats://ai-obs-demo-nats:4222
    LITELLM_URL            http://ai-obs-demo-litellm:4000
    LITELLM_KEY            sk-demo-master-key-change-me
    WORKER_CONCURRENCY     4
    OTEL_EXPORTER_OTLP_ENDPOINT  http://ai-obs-demo-alloy:4317
    METRICS_PORT           9100
"""

import asyncio
import json
import logging
import os
import signal
import time
from contextlib import asynccontextmanager

import httpx
import nats
from nats.js.api import (
    ConsumerConfig,
    DeliverPolicy,
    AckPolicy,
)
from prometheus_client import Histogram, start_http_server

from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

# ── Configuration ─────────────────────────────────────────────────────────────

NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
LITELLM_URL = os.environ.get("LITELLM_URL", "http://localhost:4000")
LITELLM_KEY = os.environ.get("LITELLM_KEY", "sk-demo-master-key-change-me")
WORKER_CONCURRENCY = int(os.environ.get("WORKER_CONCURRENCY", "4"))
OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9100"))

STREAM_NAME = "inference"
SUBJECT = "inference.request"
CONSUMER_NAME = "inference-worker"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("nats-worker")

# ── OpenTelemetry setup ───────────────────────────────────────────────────────

resource = Resource.create({"service.name": "nats-worker"})
provider = TracerProvider(resource=resource)
exporter = OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True)
provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("nats-worker")
propagator = TraceContextTextMapPropagator()

# ── Prometheus histogram ──────────────────────────────────────────────────────

QUEUE_WAIT_HISTOGRAM = Histogram(
    "nats_queue_wait_ms",
    "Time a request spent waiting in the NATS queue before processing (ms)",
    buckets=[10, 50, 100, 250, 500, 1000, 2000, 3000, 5000, 8000, 10000, 15000, 20000],
)

# ── Stream & consumer setup ──────────────────────────────────────────────────

async def ensure_stream(js):
    """Create or update the inference JetStream stream."""
    try:
        await js.find_stream_name_by_subject(SUBJECT)
        log.info(f"Stream '{STREAM_NAME}' already exists")
    except Exception:
        await js.add_stream(
            name=STREAM_NAME,
            subjects=[SUBJECT],
            retention="workqueue",
            storage="memory",
            max_age=120,  # seconds
        )
        log.info(f"Created stream '{STREAM_NAME}'")


async def ensure_consumer(js):
    """Create or update the durable pull consumer."""
    try:
        await js.consumer_info(STREAM_NAME, CONSUMER_NAME)
        log.info(f"Consumer '{CONSUMER_NAME}' already exists")
    except Exception:
        await js.add_consumer(
            STREAM_NAME,
            ConsumerConfig(
                durable_name=CONSUMER_NAME,
                deliver_policy=DeliverPolicy.ALL,
                ack_policy=AckPolicy.EXPLICIT,
                ack_wait=90,  # seconds — must exceed LiteLLM timeout (60s)
                max_deliver=3,
                filter_subject=SUBJECT,
            ),
        )
        log.info(f"Created consumer '{CONSUMER_NAME}'")


# ── Message processing ───────────────────────────────────────────────────────

async def process_message(msg, http_client: httpx.AsyncClient):
    """
    Process a single NATS message:
    1. Compute queue wait time from enqueue_time_ns header
    2. Create OTEL child span with queue_wait_ms attribute
    3. Call LiteLLM
    4. Reply with the response
    5. Ack the message
    """
    try:
        body = json.loads(msg.data.decode())
    except Exception as e:
        log.error(f"Failed to parse message: {e}")
        await msg.ack()
        return

    # Extract timing and trace context from headers
    headers = msg.headers or {}
    enqueue_time_ns = int(headers.get("Enqueue-Time-Ns", "0"))
    traceparent = headers.get("Traceparent", "")

    # Compute queue wait
    now_ns = time.time_ns()
    queue_wait_ms = (now_ns - enqueue_time_ns) / 1e6 if enqueue_time_ns > 0 else 0.0

    # Record in Prometheus
    QUEUE_WAIT_HISTOGRAM.observe(queue_wait_ms)

    # Extract parent OTEL context
    parent_ctx = None
    if traceparent:
        carrier = {"traceparent": traceparent}
        parent_ctx = propagator.extract(carrier)

    ctx_kwargs = {"context": parent_ctx} if parent_ctx else {}

    with tracer.start_as_current_span(
        "nats-worker-process",
        kind=trace.SpanKind.CONSUMER,
        attributes={
            "queue_wait_ms": round(queue_wait_ms, 1),
            "messaging.system": "nats",
            "messaging.destination": SUBJECT,
        },
        **ctx_kwargs,
    ) as span:
        # Forward to LiteLLM
        litellm_headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LITELLM_KEY}",
        }
        # Inject current span context for LiteLLM
        propagator.inject(litellm_headers)

        try:
            resp = await http_client.post(
                f"{LITELLM_URL}/v1/chat/completions",
                json=body,
                headers=litellm_headers,
                timeout=60.0,
            )
            resp.raise_for_status()
            response_data = resp.json()

            # Add usage info to span
            usage = response_data.get("usage", {})
            span.set_attribute("llm.usage.input_tokens", usage.get("prompt_tokens", 0))
            span.set_attribute("llm.usage.output_tokens", usage.get("completion_tokens", 0))

            reply_payload = json.dumps(response_data).encode()

        except Exception as e:
            log.error(f"LiteLLM call failed: {e}")
            span.set_status(trace.StatusCode.ERROR, str(e))
            reply_payload = json.dumps({
                "error": {"message": str(e), "type": "worker_error"},
            }).encode()

        # Reply to the caller via the Reply-To header (inbox subject)
        reply_to = headers.get("Reply-To", "")
        if reply_to:
            await msg._client.publish(reply_to, reply_payload)
        elif msg.reply:
            await msg._client.publish(msg.reply, reply_payload)

        await msg.ack()

        if queue_wait_ms > 1000:
            log.info(f"Processed request | queue_wait={queue_wait_ms:.0f}ms")


# ── Worker loop ──────────────────────────────────────────────────────────────

async def worker_loop(js, worker_id: int, http_client: httpx.AsyncClient, shutdown_event: asyncio.Event):
    """
    Single worker coroutine. Pulls one message at a time from the
    JetStream consumer, processes it, then pulls the next.
    """
    sub = await js.pull_subscribe(SUBJECT, CONSUMER_NAME)
    log.info(f"Worker-{worker_id} started, pulling from {SUBJECT}")

    while not shutdown_event.is_set():
        try:
            msgs = await sub.fetch(batch=1, timeout=1.0)
            for msg in msgs:
                await process_message(msg, http_client)
        except nats.errors.TimeoutError:
            # No messages available — normal during idle
            continue
        except Exception as e:
            log.error(f"Worker-{worker_id} error: {e}")
            await asyncio.sleep(1)

    log.info(f"Worker-{worker_id} shutting down")


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    log.info(f"NATS Worker starting")
    log.info(f"  NATS:        {NATS_URL}")
    log.info(f"  LiteLLM:     {LITELLM_URL}")
    log.info(f"  Concurrency: {WORKER_CONCURRENCY}")
    log.info(f"  Metrics:     :{METRICS_PORT}/metrics")

    # Start Prometheus metrics server
    start_http_server(METRICS_PORT)

    # Connect to NATS
    nc = await nats.connect(NATS_URL)
    js = nc.jetstream()
    log.info("Connected to NATS")

    # Ensure stream and consumer exist
    await ensure_stream(js)
    await ensure_consumer(js)

    # Shutdown event
    shutdown_event = asyncio.Event()

    def signal_handler():
        log.info("Shutdown signal received")
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    # Launch worker coroutines
    async with httpx.AsyncClient() as http_client:
        tasks = [
            asyncio.create_task(worker_loop(js, i, http_client, shutdown_event))
            for i in range(WORKER_CONCURRENCY)
        ]

        # Wait for shutdown
        await shutdown_event.wait()

        # Cancel workers
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # Cleanup
    provider.shutdown()
    await nc.drain()
    log.info("Worker shut down cleanly")


if __name__ == "__main__":
    asyncio.run(main())
