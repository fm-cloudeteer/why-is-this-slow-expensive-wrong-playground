"""
Phase: baseline
Duration: 3 minutes
Purpose: establish healthy baseline — all tenants at normal load,
         queues empty, quality scores high, no anomalies.
         This is the 'before' state visible in Grafana.
"""

import asyncio

from config import DemoConfig
from utils.feature_flags import FeatureFlagClient
from utils.load_generator import LoadGenerator
from utils.logger import DemoLogger
from prompts.normal import normal_prompt


async def run_baseline(
    config: DemoConfig,
    flags: FeatureFlagClient,
    logger: DemoLogger,
) -> None:
    logger.info("Ensuring all flags are off — clean baseline state")
    await flags.set("prompt_regression_active", False)
    await flags.set("expensive_tenant_active", False)

    # Small settle time — let any previous traffic drain
    await asyncio.sleep(5)

    generator = LoadGenerator(config, logger, prompt_fn=normal_prompt)
    await generator.run(
        duration_seconds=config.baseline_duration_seconds,
        rps_override=config.baseline_rps_total,
        log_interval_seconds=30,
    )
    logger.info("Baseline phase complete — healthy state established")
