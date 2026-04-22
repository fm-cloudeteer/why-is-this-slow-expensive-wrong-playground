"""
Phase: expensive
Duration: 3 minutes
Purpose: switch tenant_c to padded prompts so their token spend
         becomes a visible outlier in Langfuse cost attribution.

What happens:
  - tenant_c sends prompts with ~25 turns of fake conversation history
  - input tokens: ~1,500 vs ~80 for other tenants
  - request count: identical to other tenants
  - Langfuse cost view: tenant_c bar is ~4x taller than peers
  - latency: slightly higher for tenant_c (longer prefill), but not alarming
  - errors: 0
"""

import asyncio

from config import DemoConfig
from utils.feature_flags import FeatureFlagClient
from utils.load_generator import LoadGenerator
from utils.logger import DemoLogger
from prompts.normal import normal_prompt
from prompts.expensive import expensive_prompt


def make_prompt_fn(flags_snapshot: dict):
    """
    Returns a prompt function that routes tenant_c to expensive prompts
    and all others to normal prompts.
    """
    def prompt_fn(tenant_id: str) -> str:
        if tenant_id == "tenant_c" and flags_snapshot.get("expensive_tenant_active"):
            return expensive_prompt(tenant_id)
        return normal_prompt(tenant_id)
    return prompt_fn


def make_max_tokens_fn(flags_snapshot: dict):
    """
    Returns a max_tokens function that gives tenant_c a larger output budget
    when the expensive flag is active.
    """
    def max_tokens_fn(tenant_id: str) -> int:
        if tenant_id == "tenant_c" and flags_snapshot.get("expensive_tenant_active"):
            return 1024
        return 256
    return max_tokens_fn


async def run_expensive(
    config: DemoConfig,
    flags: FeatureFlagClient,
    logger: DemoLogger,
) -> None:
    logger.info("Activating expensive prompt profile for tenant_c")
    await flags.set("expensive_tenant_active", True)

    logger.info(
        "tenant_c now sending ~1,500 input tokens per request "
        "(others: ~80). Same RPS, same model, same error rate."
    )
    logger.info("Watch Langfuse cost attribution — tenant_c bar will diverge")

    flags_snapshot = {"expensive_tenant_active": True}
    generator = LoadGenerator(
        config,
        logger,
        prompt_fn=make_prompt_fn(flags_snapshot),
        max_tokens_fn=make_max_tokens_fn(flags_snapshot),
    )
    await generator.run(
        duration_seconds=config.expensive_duration_seconds,
        rps_override=config.baseline_rps_total,
        log_interval_seconds=30,
    )
    logger.info("Expensive phase complete — token divergence established in Langfuse")
    logger.info("Flag left ON — anomaly remains visible in cost panels")
