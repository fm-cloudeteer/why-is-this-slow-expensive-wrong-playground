"""
Demo app — the distributed application layer (Layer 3).

This FastAPI service sits behind Gravitee and in front of the NATS
inference queue. It is where the observability story for Layer 3 lives:
  - Injects system prompts (and swaps them based on feature flags)
  - Publishes inference requests to NATS JetStream (picked up by the worker)
  - Attaches Langfuse traces with token counts and quality scores
  - Propagates the W3C traceparent header from Gravitee through NATS
  - Emits OTEL spans to Tempo (via Alloy) as children of the Gravitee trace

Feature flags (controlled by orchestrator.py):
  prompt_regression_active  → swaps tenant_b to degraded system prompt
  expensive_tenant_active   → no-op here; expensive prompts come from
                              the load generator directly

Run:
    uvicorn demo_app.main:app --host 0.0.0.0 --port 8080
"""

import os
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from langfuse import Langfuse
from opentelemetry import trace, context
from opentelemetry.context.contextvars_context import ContextVarsRuntimeContext
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from prometheus_client import Counter, Gauge, generate_latest, CONTENT_TYPE_LATEST
from pydantic import BaseModel
from starlette.responses import Response

from demo_app.flags import FlagStore, router as flags_router
from demo_app.prompts import get_system_prompt
from demo_app.scorer import score_response
from demo_app import nats_client


# ── OpenTelemetry setup ────────────────────────────────────────────────────

OTLP_ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://ai-obs-demo-alloy:4317")

resource = Resource.create({"service.name": "demo-app"})
provider = TracerProvider(resource=resource)
exporter = OTLPSpanExporter(endpoint=OTLP_ENDPOINT, insecure=True)
provider.add_span_processor(BatchSpanProcessor(exporter))
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("demo-app")
propagator = TraceContextTextMapPropagator()


# ── Prometheus metrics ─────────────────────────────────────────────────────

TOKENS_IN = Counter(
    "litellm_input_tokens_total", "Input tokens consumed", ["tenant_id"],
)
TOKENS_OUT = Counter(
    "litellm_output_tokens_total", "Output tokens consumed", ["tenant_id"],
)
REQUESTS_TOTAL = Counter(
    "litellm_requests_total", "Inference requests", ["tenant_id", "status"],
)
QUALITY_SCORE = Gauge(
    "langfuse_score", "Quality score (last observation)", ["tenant_id", "score_name"],
)


def extract_context_from_traceparent(traceparent: Optional[str]):
    """Parse a W3C traceparent header and return an OTel context."""
    if not traceparent:
        return None
    carrier = {"traceparent": traceparent}
    return propagator.extract(carrier)


def inject_traceparent(span_context) -> dict:
    """Inject the current span's context into a traceparent header dict."""
    headers = {}
    propagator.inject(headers)
    return headers


# ── Startup / shutdown ──────────────────────────────────────────────────────

flag_store = FlagStore()
langfuse_client: Optional[Langfuse] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global langfuse_client
    langfuse_client = Langfuse(
        public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
        secret_key=os.environ["LANGFUSE_SECRET_KEY"],
        base_url=os.environ.get("LANGFUSE_URL", "http://localhost:3000"),
    )

    # Connect to NATS
    await nats_client.connect()

    yield

    # Shutdown
    await nats_client.close()
    if langfuse_client:
        langfuse_client.flush()
        langfuse_client.shutdown()
    provider.shutdown()


app = FastAPI(title="Demo app", lifespan=lifespan)
app.include_router(flags_router)

LITELLM_URL = os.environ.get("LITELLM_URL", "http://localhost:4000")
LITELLM_KEY = os.environ.get("LITELLM_KEY", "sk-demo-master-key-change-me")


# ── Request / response models ───────────────────────────────────────────────

class ChatRequest(BaseModel):
    model: str = "cyankiwi/Qwen3.5-9B-AWQ-4bit"
    messages: list[dict]
    max_tokens: int = 1024
    stream: bool = False


# ── Routes ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/metrics")
async def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/v1/chat/completions")
async def chat(
    body: ChatRequest,
    request: Request,
    x_tenant_id: str = Header(default="unknown"),
    traceparent: Optional[str] = Header(default=None),
):
    # Extract parent context from Gravitee's traceparent header
    parent_ctx = extract_context_from_traceparent(traceparent)
    ctx_kwargs = {"context": parent_ctx} if parent_ctx else {}

    with tracer.start_as_current_span(
        "chat-completion",
        kind=trace.SpanKind.SERVER,
        attributes={
            "tenant.id": x_tenant_id,
            "llm.model": body.model,
            "llm.max_tokens": body.max_tokens,
        },
        **ctx_kwargs,
    ) as root_span:
        start_time = time.monotonic()

        # Inject system prompt — swapped for tenant_b if flag is active
        system_prompt = get_system_prompt(
            tenant_id=x_tenant_id,
            regression_active=flag_store.get("prompt_regression_active"),
        )
        messages_with_system = [
            {"role": "system", "content": system_prompt},
            *body.messages,
        ]

        # Get the traceparent for the current span to pass downstream + store in Langfuse
        downstream_headers: dict = {}
        propagator.inject(downstream_headers)
        current_traceparent = downstream_headers.get("traceparent", traceparent)

        # Start Langfuse trace (v4 SDK)
        trace_span = langfuse_client.start_observation(
            name="chat-completion",
            as_type="span",
            input={"messages": body.messages, "model": body.model},
            metadata={
                "tenant_id": x_tenant_id,
                "traceparent": current_traceparent,
                "model": body.model,
            },
        )

        generation = trace_span.start_observation(
            name="llm-call",
            as_type="generation",
            model=body.model,
            input=messages_with_system,
        )

        # Publish to NATS queue — as a child span
        with tracer.start_as_current_span(
            "nats-enqueue",
            kind=trace.SpanKind.PRODUCER,
            attributes={
                "messaging.system": "nats",
                "messaging.destination": "inference.request",
            },
        ):
            # Get traceparent for the NATS message (child of nats-enqueue span)
            nats_headers: dict = {}
            propagator.inject(nats_headers)
            nats_traceparent = nats_headers.get("traceparent", "")

            nats_payload = {
                "model": body.model,
                "messages": messages_with_system,
                "max_tokens": body.max_tokens,
            }

            try:
                data = await nats_client.request(
                    payload=nats_payload,
                    traceparent=nats_traceparent,
                )
            except TimeoutError as e:
                root_span.set_status(trace.StatusCode.ERROR, str(e))
                generation.update(level="ERROR", status_message=str(e))
                generation.end()
                trace_span.update(output={"error": str(e)})
                trace_span.end()
                REQUESTS_TOTAL.labels(tenant_id=x_tenant_id, status="error").inc()
                raise HTTPException(status_code=504, detail=str(e))
            except Exception as e:
                root_span.set_status(trace.StatusCode.ERROR, str(e))
                generation.update(level="ERROR", status_message=str(e))
                generation.end()
                trace_span.update(output={"error": str(e)})
                trace_span.end()
                REQUESTS_TOTAL.labels(tenant_id=x_tenant_id, status="error").inc()
                raise HTTPException(status_code=502, detail=str(e))

        latency_ms = (time.monotonic() - start_time) * 1000

        # Extract usage
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        # Quality scoring (synchronous, rule-based — deterministic for demo)
        response_text = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        user_prompt = " ".join(
            str(m.get("content", "")) for m in body.messages if m.get("role") == "user"
        )
        quality_score = score_response(response_text, x_tenant_id, user_prompt)

        # Record Prometheus metrics
        TOKENS_IN.labels(tenant_id=x_tenant_id).inc(input_tokens)
        TOKENS_OUT.labels(tenant_id=x_tenant_id).inc(output_tokens)
        REQUESTS_TOTAL.labels(tenant_id=x_tenant_id, status="success").inc()
        QUALITY_SCORE.labels(tenant_id=x_tenant_id, score_name="quality").set(quality_score)

        # Record in OTEL span
        root_span.set_attribute("llm.usage.input_tokens", input_tokens)
        root_span.set_attribute("llm.usage.output_tokens", output_tokens)
        root_span.set_attribute("llm.usage.total_tokens", input_tokens + output_tokens)
        root_span.set_attribute("llm.quality_score", quality_score)
        root_span.set_attribute("llm.latency_ms", round(latency_ms))

        # End Langfuse generation with full token counts
        generation.update(
            output=response_text,
            usage_details={
                "input": input_tokens,
                "output": output_tokens,
                "total": input_tokens + output_tokens,
            },
            metadata={
                "latency_ms": round(latency_ms),
                "quality_score": quality_score,
                "tenant_id": x_tenant_id,
            },
        )
        generation.end()

        # Attach quality score to the trace (shows in Langfuse trend charts)
        trace_span.score_trace(
            name="quality",
            value=quality_score,
            comment=f"tenant={x_tenant_id} tokens_in={input_tokens}",
        )

        trace_span.update(output={"response": response_text[:200]})
        trace_span.end()

        return JSONResponse(content=data)
