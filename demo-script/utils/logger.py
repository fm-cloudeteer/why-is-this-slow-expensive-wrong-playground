"""
Demo logger — coloured terminal output with phase banners.
Timestamps every line so the terminal log matches the manifest.
"""

import sys
from datetime import datetime, timezone


RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
CYAN   = "\033[36m"
BLUE   = "\033[34m"


class DemoLogger:
    def _ts(self) -> str:
        return datetime.now(timezone.utc).strftime("%H:%M:%S")

    def info(self, msg: str) -> None:
        print(f"{self._ts()}  {msg}", flush=True)

    def success(self, msg: str) -> None:
        print(f"{self._ts()}  {GREEN}✓{RESET}  {msg}", flush=True)

    def warn(self, msg: str) -> None:
        print(f"{self._ts()}  {YELLOW}⚠{RESET}  {msg}", flush=True)

    def error(self, msg: str) -> None:
        print(f"{self._ts()}  {RED}✗{RESET}  {msg}", file=sys.stderr, flush=True)

    def phase(self, name: str, description: str) -> None:
        bar = "─" * 60
        print(f"\n{CYAN}{bar}{RESET}", flush=True)
        print(
            f"{CYAN}{self._ts()}  PHASE: {BOLD}{name.upper()}{RESET}"
            f"{CYAN}  —  {description}{RESET}",
            flush=True,
        )
        print(f"{CYAN}{bar}{RESET}\n", flush=True)

    def banner(self, msg: str) -> None:
        bar = "═" * 60
        print(f"\n{BLUE}{bar}{RESET}", flush=True)
        print(f"{BLUE}{BOLD}  {msg}{RESET}", flush=True)
        print(f"{BLUE}{bar}{RESET}\n", flush=True)
