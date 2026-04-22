"""
Phase: recovery
Duration: 2 minutes
Purpose: return to clean baseline load so Grafana shows the full
         mountain shape — spike up, then recovery back to normal.
         Flags are intentionally left ON so the failure evidence
         remains visible in the historical window.

Note: we do NOT reset flags here. The prompt regression and expensive
tenant anomalies should remain visible in Langfuse for the demo.
Only the load returns to normal.
"""

import asyncio

from config import DemoConfig
from utils.feature_flags import FeatureFlagClient
from utils.load_generator import LoadGenerator
from utils.logger import DemoLogger
from prompts.normal import normal_prompt
from prompts.expensive import expensive_prompt


def make_prompt_fn():
    def prompt_fn(tenant_id: str) -> str:
        # Keep tenant_c on expensive prompts so cost panel stays anomalous
        if tenant_id == "tenant_c":
            return expensive_prompt(tenant_id)
        return normal_prompt(tenant_id)
    return prompt_fn


async def run_recovery(
    config: DemoConfig,
    flags: FeatureFlagClient,
    logger: DemoLogger,
) -> None:
    logger.info("Returning to baseline load — queues draining")
    logger.info("Flags left ON: prompt_regression_active, expensive_tenant_active")
    logger.info("Failure evidence remains in Langfuse and Grafana historical view")

    generator = LoadGenerator(config, logger, prompt_fn=make_prompt_fn())
    await generator.run(
        duration_seconds=config.recovery_duration_seconds,
        rps_override=config.baseline_rps_total,
        log_interval_seconds=30,
    )
    logger.info("Recovery complete — system back to healthy baseline")
    logger.info(
        "Grafana now shows: baseline → regression → cost anomaly → spike → recovery"
    )
