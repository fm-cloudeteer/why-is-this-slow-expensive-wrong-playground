"""
Phase: wrong
Duration: 4 minutes
Purpose: flip prompt regression flag for tenant_b and let quality
         scores visibly degrade. All infrastructure metrics stay healthy —
         that's the point. Only the quality scorer catches this.

What happens:
  - tenant_b's system prompt is swapped to a degraded version
    by the demo app's feature flag check
  - quality scorer starts returning scores ~0.2 vs baseline ~0.9
  - Langfuse quality trend shows a clear step-change at phase start
  - error rate: 0   latency: unchanged   GPU: unchanged
"""

import asyncio
from datetime import datetime, timezone

from config import DemoConfig
from utils.feature_flags import FeatureFlagClient
from utils.load_generator import LoadGenerator
from utils.logger import DemoLogger
from prompts.normal import normal_prompt


async def run_wrong(
    config: DemoConfig,
    flags: FeatureFlagClient,
    logger: DemoLogger,
) -> None:
    logger.info("Injecting prompt regression for tenant_b")
    regression_time = datetime.now(timezone.utc)
    await flags.set("prompt_regression_active", True)

    logger.info(
        f"Flag flipped at {regression_time.strftime('%H:%M:%S')} UTC — "
        f"watch Langfuse quality scores drop for tenant_b"
    )
    logger.info(
        "Infrastructure metrics (latency, errors, GPU) will stay flat — "
        "that's the demo point"
    )

    # Run normal load — no change to request rate or prompt content
    # The degradation is entirely in the system prompt swap inside the demo app
    generator = LoadGenerator(config, logger, prompt_fn=normal_prompt)
    await generator.run(
        duration_seconds=config.wrong_duration_seconds,
        rps_override=config.baseline_rps_total,
        log_interval_seconds=30,
    )

    logger.info(
        f"Wrong phase complete. Step-change visible in Langfuse "
        f"from {regression_time.strftime('%H:%M:%S')} UTC onward"
    )
    logger.info("Flag left ON — regression remains active into next phases")
