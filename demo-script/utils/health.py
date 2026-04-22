"""
Stack health checker.
Verifies all demo services are reachable before starting a run.
Fails fast with a clear error rather than discovering problems mid-phase.
"""

import asyncio
import aiohttp

from config import DemoConfig
from utils.logger import DemoLogger


CHECKS = [
    ("Gateway",    lambda c: f"{c.gateway_url}/health"),
    ("Langfuse",   lambda c: f"{c.langfuse_url}/api/public/health"),
    ("Prometheus", lambda c: f"{c.prometheus_url}/-/healthy"),
    ("Tempo",      lambda c: f"{c.tempo_url}/ready"),
    ("FeatureFlags", lambda c: f"{c.feature_flag_url}/health"),
]


async def _check(
    session: aiohttp.ClientSession,
    name: str,
    url: str,
    logger: DemoLogger,
) -> bool:
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status < 500:
                logger.success(f"{name} reachable ({url})")
                return True
            logger.error(f"{name} returned {resp.status} ({url})")
            return False
    except Exception as e:
        logger.error(f"{name} unreachable: {e} ({url})")
        return False


async def wait_for_stack(
    config: DemoConfig,
    logger: DemoLogger,
    retries: int = 3,
    retry_delay: float = 5.0,
) -> None:
    """
    Checks all services. Retries up to `retries` times.
    Raises RuntimeError if any service remains unreachable.
    """
    for attempt in range(1, retries + 1):
        async with aiohttp.ClientSession() as session:
            results = await asyncio.gather(*[
                _check(session, name, fn(config), logger)
                for name, fn in CHECKS
            ])

        if all(results):
            return

        failed = [CHECKS[i][0] for i, ok in enumerate(results) if not ok]
        if attempt < retries:
            logger.warn(
                f"Attempt {attempt}/{retries}: "
                f"{', '.join(failed)} not ready — retrying in {retry_delay}s"
            )
            await asyncio.sleep(retry_delay)
        else:
            raise RuntimeError(
                f"Stack health check failed after {retries} attempts. "
                f"Services unreachable: {', '.join(failed)}"
            )
