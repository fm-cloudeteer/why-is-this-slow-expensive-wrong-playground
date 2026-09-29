"""
Phase: slow
Duration: 3 minutes (90s burst + 90s drain)
Purpose: fire a request burst just above sustainable throughput
         so that NATS queue depth climbs, p99 spikes, and individual
         traces show multi-second queue wait spans.

What happens:
  - RPS jumps from ~4 to ~30 for 90 seconds
  - NATS queue depth climbs visibly (target: 20-40 messages deep)
  - vllm:num_requests_waiting also climbs
  - p99 latency spikes to 5-10s (from ~800ms baseline)
  - GPU utilization goes high but not maxed — compute is not the bottleneck
  - Individual traces: queue wait span 3-8 seconds, inference span unchanged
  - After 90s burst ends, queue drains and p99 recovers — clean mountain shape

The hero trace lives in this phase. The find_hero_trace.py script
queries Tempo for the request with the longest queue_wait_ms during
this window.
"""

import asyncio
from datetime import datetime, timezone

from config import DemoConfig
from utils.feature_flags import FeatureFlagClient
from utils.load_generator import LoadGenerator
from utils.logger import DemoLogger
from prompts.expensive import expensive_prompt
from prompts.normal import normal_prompt


def make_prompt_fn(expensive_active: bool):
    def prompt_fn(tenant_id: str) -> str:
        if tenant_id == "tenant_c" and expensive_active:
            return expensive_prompt(tenant_id)
        return normal_prompt(tenant_id)
    return prompt_fn


async def run_slow(
    config: DemoConfig,
    flags: FeatureFlagClient,
    logger: DemoLogger,
) -> None:
    burst_rps = config.burst_rps_total
    burst_duration = config.burst_duration_seconds
    drain_duration = config.slow_duration_seconds - burst_duration  # remaining time

    # ── Burst ──────────────────────────────────────────────────────────────
    logger.info(f"Firing burst: {burst_rps:.0f} RPS for {burst_duration}s")
    logger.info(
        f"Baseline: {config.baseline_rps_total:.0f} RPS — "
        f"burst is {burst_rps / config.baseline_rps_total:.1f}x baseline"
    )

    burst_start = datetime.now(timezone.utc)
    logger.info(
        f"Burst start: {burst_start.strftime('%H:%M:%S')} UTC — "
        f"this is the window to find your hero trace"
    )

    generator = LoadGenerator(
        config,
        logger,
        prompt_fn=make_prompt_fn(expensive_active=True),
    )
    await generator.run(
        duration_seconds=burst_duration,
        rps_override=burst_rps,
        log_interval_seconds=15,
    )

    burst_end = datetime.now(timezone.utc)
    logger.info(f"Burst ended at {burst_end.strftime('%H:%M:%S')} UTC")
    logger.info("Queue draining — watch p99 recover on Grafana")

    # ── Drain ─────────────────────────────────────────────────────────────
    # Run at normal rate while queue drains — gives the mountain shape
    if drain_duration > 0:
        logger.info(f"Running drain traffic for {drain_duration}s at baseline RPS")
        await generator.run(
            duration_seconds=drain_duration,
            rps_override=config.baseline_rps_total,
            log_interval_seconds=30,
        )

    logger.info("Slow phase complete")
    logger.info(
        f"Hero trace window: "
        f"{burst_start.strftime('%H:%M:%S')} — "
        f"{burst_end.strftime('%H:%M:%S')} UTC"
    )
    logger.info("Run: python find_hero_trace.py to identify best trace for Tab 3")
