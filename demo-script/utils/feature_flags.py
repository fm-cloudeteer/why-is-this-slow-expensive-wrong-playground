"""
Feature flag client.
Talks to a lightweight flag endpoint in the demo app that controls:
  - prompt_regression_active  → swaps tenant_b's system prompt
  - expensive_tenant_active   → routes tenant_c to expensive prompts

The demo app checks these flags on every request. The flag service
can be as simple as a FastAPI endpoint backed by a dict in memory —
see demo_app/flags.py for the reference implementation.
"""

import aiohttp
from utils.logger import DemoLogger


class FeatureFlagClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    async def set(self, flag: str, value: bool) -> None:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/flags/{flag}",
                json={"value": value},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                resp.raise_for_status()

    async def get(self, flag: str) -> bool:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/flags/{flag}",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data["value"]

    async def reset_all(self) -> None:
        """Reset all flags to safe defaults before a run."""
        await self.set("prompt_regression_active", False)
        await self.set("expensive_tenant_active", False)
