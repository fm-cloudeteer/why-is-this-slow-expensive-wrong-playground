#!/usr/bin/env python3
"""
Demo orchestrator — "Why Is This Slow, Expensive, and Wrong?"

Runs all three failure patterns in sequence and writes a timestamped
run manifest you can use to pre-stage Grafana time ranges and find
the hero trace in Tempo afterward.

Usage:
    python orchestrator.py                        # full run
    python orchestrator.py --phase slow           # single phase
    python orchestrator.py --phase expensive
    python orchestrator.py --phase wrong
    python orchestrator.py --dry-run              # validate config only

Phases:
    1. baseline      3 min  clean traffic, all tenants healthy
    2. wrong         4 min  prompt regression injected for tenant_b
    3. expensive     3 min  token-heavy prompts for tenant_c added
    4. slow          3 min  burst load — queue spike + p99 climb
    5. recovery      2 min  return to baseline, queues drain

Total wall-clock time: ~15 minutes
"""

import argparse
import asyncio
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from config import DemoConfig
from phases.baseline import run_baseline
from phases.expensive import run_expensive
from phases.recovery import run_recovery
from phases.slow import run_slow
from phases.wrong import run_wrong
from utils.feature_flags import FeatureFlagClient
from utils.logger import DemoLogger
from utils.manifest import ManifestWriter
from utils.health import wait_for_stack


PHASES = {
    "baseline":  (run_baseline,  "3m",  "Clean traffic — establish healthy baseline"),
    "wrong":     (run_wrong,     "4m",  "Prompt regression injected for tenant_b"),
    "expensive": (run_expensive, "3m",  "Token-heavy prompts added for tenant_c"),
    "slow":      (run_slow,      "3m",  "Burst load — queue spike + p99 latency"),
    "recovery":  (run_recovery,  "2m",  "Return to baseline, queues drain"),
}


async def main(args: argparse.Namespace) -> None:
    config = DemoConfig.from_env()
    logger = DemoLogger()
    manifest = ManifestWriter(Path("run_manifest.json"))
    flags = FeatureFlagClient(config.feature_flag_url)

    logger.banner("Demo orchestrator starting")
    logger.info(f"Target stack:  {config.gateway_url}")
    logger.info(f"Langfuse:      {config.langfuse_url}")
    logger.info(f"Prometheus:    {config.prometheus_url}")
    logger.info(f"Tempo:         {config.tempo_url}")

    if args.dry_run:
        logger.info("Dry run — validating config and stack health only")
        await wait_for_stack(config, logger)
        logger.success("All services reachable. Ready to run.")
        return

    # Reset all feature flags to known-good state before starting
    await flags.reset_all()
    logger.info("Feature flags reset to defaults")

    # Health check — abort early rather than fail mid-demo
    logger.info("Checking stack health...")
    await wait_for_stack(config, logger)
    logger.success("Stack healthy — proceeding")

    run_start = datetime.now(timezone.utc)
    manifest.write_start(run_start)

    phases_to_run = (
        list(PHASES.keys())
        if args.phase == "all"
        else ["baseline", args.phase, "recovery"]
    )

    # Register SIGINT handler so Ctrl-C still writes the manifest
    def handle_interrupt(sig, frame):
        logger.warn("Interrupted — writing partial manifest")
        manifest.write_end(datetime.now(timezone.utc))
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_interrupt)

    for phase_name in phases_to_run:
        fn, duration, description = PHASES[phase_name]
        logger.phase(phase_name, description)

        phase_start = datetime.now(timezone.utc)
        manifest.write_phase_start(phase_name, phase_start)

        try:
            await fn(config, flags, logger)
        except Exception as e:
            logger.error(f"Phase {phase_name} failed: {e}")
            manifest.write_phase_end(phase_name, datetime.now(timezone.utc), error=str(e))
            raise

        phase_end = datetime.now(timezone.utc)
        manifest.write_phase_end(phase_name, phase_end)
        logger.success(f"Phase {phase_name} complete ({phase_end - phase_start})")

    run_end = datetime.now(timezone.utc)
    manifest.write_end(run_end)

    logger.banner("Run complete")
    logger.info(f"Total runtime: {run_end - run_start}")
    logger.info(f"Manifest written to: run_manifest.json")
    logger.info("")
    logger.info("Next steps:")
    logger.info("  1. Run: python find_hero_trace.py")
    logger.info("     → finds the best queue-wait trace for Tab 3")
    logger.info("  2. Open Grafana and set time range to:")
    logger.info(f"     From: {run_start.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    logger.info(f"     To:   {run_end.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    logger.info("  3. Verify all five pre-staged tabs look correct")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Demo orchestrator")
    parser.add_argument(
        "--phase",
        choices=["all", "slow", "expensive", "wrong"],
        default="all",
        help="Run a single failure phase (wraps in baseline + recovery)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and stack health only, do not run",
    )
    args = parser.parse_args()
    asyncio.run(main(args))
