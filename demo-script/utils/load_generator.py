"""
Async load generator — fires requests against the gateway at a
controlled rate, attaches tenant headers, and records basic
per-request telemetry to stdout.
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

import aiohttp

from config import DemoConfig, TenantConfig
from utils.logger import DemoLogger


@dataclass
class RequestResult:
    tenant_id: str
    trace_id: str
    status: int
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    error: Optional[str] = None


@dataclass
class LoadStats:
    total: int = 0
    success: int = 0
    errors: int = 0
    total_latency_ms: float = 0.0
    results: list = field(default_factory=list)

    @property
    def p99_ms(self) -> float:
        if not self.results:
            return 0.0
        latencies = sorted(r.latency_ms for r in self.results if r.error is None)
        if not latencies:
            return 0.0
        idx = int(len(latencies) * 0.99)
        return latencies[min(idx, len(latencies) - 1)]

    @property
    def mean_ms(self) -> float:
        successful = [r for r in self.results if r.error is None]
        if not successful:
            return 0.0
        return sum(r.latency_ms for r in successful) / len(successful)


class LoadGenerator:
    """
    Fires requests at a given RPS against the Gravitee gateway.
    Supports per-tenant RPS weighting and prompt overrides.
    """

    # Abort early if the first N requests for any tenant all fail
    EARLY_ABORT_THRESHOLD = 10

    def __init__(
        self,
        config: DemoConfig,
        logger: DemoLogger,
        prompt_fn: Optional[Callable[[str], str]] = None,
        max_tokens_fn: Optional[Callable[[str], int]] = None,
    ):
        self.config = config
        self.logger = logger
        self.prompt_fn = prompt_fn or self._default_prompt
        self.max_tokens_fn = max_tokens_fn or (lambda tid: 128)
        self._stats: dict[str, LoadStats] = {
            tid: LoadStats() for tid in config.tenants
        }

    async def run(
        self,
        duration_seconds: int,
        rps_override: Optional[float] = None,
        tenants_override: Optional[list[str]] = None,
        log_interval_seconds: int = 15,
    ) -> dict[str, LoadStats]:
        """
        Run load for duration_seconds at the configured RPS.
        Returns per-tenant stats.
        """
        active_tenants = tenants_override or list(self.config.tenants.keys())
        total_rps = rps_override or self.config.baseline_rps_total

        # Distribute RPS across active tenants proportionally
        rps_per_tenant = total_rps / len(active_tenants)
        interval_per_tenant = 1.0 / rps_per_tenant

        self.logger.info(
            f"Load: {total_rps:.0f} RPS across {len(active_tenants)} tenants "
            f"({rps_per_tenant:.1f} RPS each) for {duration_seconds}s"
        )

        deadline = time.monotonic() + duration_seconds
        last_log = time.monotonic()

        connector = aiohttp.TCPConnector(limit=200)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [
                self._tenant_loop(
                    session,
                    self.config.tenants[tid],
                    interval_per_tenant,
                    deadline,
                )
                for tid in active_tenants
            ]

            async def logger_loop():
                nonlocal last_log
                while time.monotonic() < deadline:
                    await asyncio.sleep(log_interval_seconds)
                    self._log_stats()

            await asyncio.gather(*tasks, logger_loop())

        return self._stats

    async def _tenant_loop(
        self,
        session: aiohttp.ClientSession,
        tenant: TenantConfig,
        interval: float,
        deadline: float,
    ) -> None:
        while time.monotonic() < deadline:
            # Early abort: if the first N requests all failed, stop immediately
            stats = self._stats[tenant.tenant_id]
            if (
                stats.total >= self.EARLY_ABORT_THRESHOLD
                and stats.success == 0
            ):
                sample_errors = [
                    r.error for r in stats.results[:3] if r.error
                ]
                raise RuntimeError(
                    f"Aborting — first {stats.total} requests for "
                    f"{tenant.tenant_id} all failed. "
                    f"Sample errors: {sample_errors}"
                )
            start = time.monotonic()
            asyncio.create_task(self._fire(session, tenant))
            elapsed = time.monotonic() - start
            sleep = max(0.0, interval - elapsed)
            await asyncio.sleep(sleep)

    async def _fire(
        self,
        session: aiohttp.ClientSession,
        tenant: TenantConfig,
    ) -> RequestResult:
        trace_id = uuid.uuid4().hex
        prompt = self.prompt_fn(tenant.tenant_id)
        max_tokens = self.max_tokens_fn(tenant.tenant_id)
        start = time.monotonic()

        try:
            async with session.post(
                f"{self.config.gateway_url}/v1/chat/completions",
                json={
                    "model": tenant.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "stream": False,
                },
                headers={
                    "X-Gravitee-Api-Key": tenant.api_key,
                    "X-Tenant-ID": tenant.tenant_id,
                    "traceparent": f"00-{trace_id}{'0' * 16}-{'0' * 16}-01",
                },
                timeout=aiohttp.ClientTimeout(total=200),
            ) as resp:
                latency_ms = (time.monotonic() - start) * 1000
                body = await resp.json()

                usage = body.get("usage", {})
                result = RequestResult(
                    tenant_id=tenant.tenant_id,
                    trace_id=trace_id,
                    status=resp.status,
                    latency_ms=latency_ms,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                    error=f"HTTP {resp.status}" if resp.status >= 400 else None,
                )

        except Exception as e:
            latency_ms = (time.monotonic() - start) * 1000
            result = RequestResult(
                tenant_id=tenant.tenant_id,
                trace_id=trace_id,
                status=0,
                latency_ms=latency_ms,
                error=str(e),
            )

        stats = self._stats[tenant.tenant_id]
        stats.total += 1
        stats.results.append(result)
        if result.error:
            stats.errors += 1
        else:
            stats.success += 1
            stats.total_latency_ms += result.latency_ms

        return result

    def _log_stats(self) -> None:
        for tid, stats in self._stats.items():
            if stats.total == 0:
                continue
            self.logger.info(
                f"  {tid}: {stats.total} req | "
                f"mean {stats.mean_ms:.0f}ms | "
                f"p99 {stats.p99_ms:.0f}ms | "
                f"errors {stats.errors}"
            )

    @staticmethod
    def _default_prompt(tenant_id: str) -> str:
        return (
            "I need to reschedule my train ticket from Berlin to Munich "
            "for next Tuesday. Ticket reference BER-MCH-20241203-4421. "
            "What are my options?"
        )
