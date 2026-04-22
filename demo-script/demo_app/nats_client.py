"""
NATS client helper for the demo app.

Publishes inference requests to the NATS JetStream "inference" stream
and waits for the worker's reply on a unique inbox subject.

The pattern:
  1. Create a unique inbox subject and subscribe to it
  2. Publish to JetStream with the inbox in a header
  3. Worker pulls from JetStream, processes, publishes reply to inbox
  4. Demo app receives reply on the inbox subscription

The NATS connection is managed via connect/close functions called from
the FastAPI lifespan handler.
"""

import asyncio
import json
import logging
import os
import time
from typing import Optional

import nats
from nats.aio.client import Client as NATSClient

log = logging.getLogger("demo-app.nats")

NATS_URL = os.environ.get("NATS_URL", "nats://localhost:4222")
SUBJECT = "inference.request"
REQUEST_TIMEOUT = 180.0  # must exceed worst-case queue wait + inference time

_nc: Optional[NATSClient] = None
_js = None


async def connect():
    """Connect to NATS and get JetStream context. Call once at app startup."""
    global _nc, _js
    _nc = await nats.connect(NATS_URL)
    _js = _nc.jetstream()
    log.info(f"Connected to NATS at {NATS_URL}")


async def close():
    """Drain and close the NATS connection. Call at app shutdown."""
    global _nc, _js
    if _nc:
        await _nc.drain()
        _nc = None
        _js = None
        log.info("NATS connection closed")


async def request(
    payload: dict,
    traceparent: Optional[str] = None,
) -> dict:
    """
    Publish an inference request to NATS JetStream and wait for the
    worker's reply on a unique inbox.

    Args:
        payload: The chat completion request body (model, messages, max_tokens).
        traceparent: W3C traceparent header to propagate through the queue.

    Returns:
        The LiteLLM response dict.

    Raises:
        TimeoutError: If the worker doesn't reply within REQUEST_TIMEOUT.
        RuntimeError: If NATS is not connected.
        Exception: If the worker returns an error response.
    """
    if not _nc or _nc.is_closed:
        raise RuntimeError("NATS not connected")

    # Create a unique inbox for the reply
    inbox = _nc.new_inbox()
    reply_future: asyncio.Future = asyncio.get_event_loop().create_future()

    # Subscribe to the inbox to receive the reply
    async def on_reply(msg):
        if not reply_future.done():
            reply_future.set_result(msg)

    sub = await _nc.subscribe(inbox, cb=on_reply)

    try:
        # Build headers — include the reply inbox for the worker
        headers = {
            "Enqueue-Time-Ns": str(time.time_ns()),
            "Reply-To": inbox,
        }
        if traceparent:
            headers["Traceparent"] = traceparent

        data = json.dumps(payload).encode()

        # Publish to JetStream (gets persisted in the stream)
        await _js.publish(SUBJECT, data, headers=headers)

        # Wait for the worker's reply on the inbox
        try:
            msg = await asyncio.wait_for(reply_future, timeout=REQUEST_TIMEOUT)
        except asyncio.TimeoutError:
            raise TimeoutError(
                f"NATS request timed out after {REQUEST_TIMEOUT}s — "
                f"worker may be overloaded or not running"
            )
    finally:
        await sub.unsubscribe()

    response = json.loads(msg.data.decode())

    # Check if worker returned an error
    if "error" in response and "choices" not in response:
        error = response["error"]
        raise Exception(
            f"Worker error: {error.get('message', str(error))}"
        )

    return response
