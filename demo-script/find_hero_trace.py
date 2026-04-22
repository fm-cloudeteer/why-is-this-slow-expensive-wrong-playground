"""
Hero trace finder.

Reads run_manifest.json to get the burst window timestamps, then
queries Tempo for traces in that window and finds the one with the
longest queue_wait_ms span attribute — that's your Tab 3 hero trace.

Usage:
    python find_hero_trace.py
    python find_hero_trace.py --manifest path/to/run_manifest.json
    python find_hero_trace.py --window-start 14:23:00 --window-end 14:24:30

Output:
    Prints the Tempo deep-link URL for the best trace.
    Also writes hero_trace.json with full trace details.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests


def load_manifest(path: Path) -> dict:
    if not path.exists():
        print(f"ERROR: manifest not found at {path}")
        print("Run orchestrator.py first, or specify --window-start and --window-end")
        sys.exit(1)
    return json.loads(path.read_text())


def get_burst_window(manifest: dict) -> tuple[datetime, datetime]:
    slow_phase = manifest.get("phases", {}).get("slow", {})
    if not slow_phase:
        raise ValueError("No 'slow' phase found in manifest")
    start = datetime.fromisoformat(slow_phase["start"])
    end   = datetime.fromisoformat(slow_phase["end"])
    return start, end


def query_tempo(
    tempo_url: str,
    window_start: datetime,
    window_end: datetime,
    limit: int = 100,
) -> list[dict]:
    """
    Query Tempo's HTTP API for traces in the burst window.
    Filters for traces that have a queue_wait_ms span attribute.
    """
    params = {
        "start": int(window_start.timestamp()),  # seconds
        "end":   int(window_end.timestamp()),
        "limit": limit,
        "q": '{resource.service.name = "nats-worker"}',  # TraceQL filter
    }
    url = f"{tempo_url}/api/search"
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json().get("traces", [])


def get_trace_detail(tempo_url: str, trace_id: str) -> dict:
    url = f"{tempo_url}/api/traces/{trace_id}"
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def extract_queue_wait_ms(trace: dict) -> float:
    """Walk the trace spans and return the max queue_wait_ms attribute."""
    max_wait = 0.0
    for batch in trace.get("batches", []):
        for scope_span in batch.get("scopeSpans", []):
            for span in scope_span.get("spans", []):
                for attr in span.get("attributes", []):
                    if attr.get("key") == "queue_wait_ms":
                        val = attr.get("value", {}).get("doubleValue", 0.0)
                        max_wait = max(max_wait, val)
    return max_wait


def find_best_trace(traces: list[dict], tempo_url: str) -> tuple[dict, float]:
    """
    Fetch detail for each candidate trace and return the one with
    the longest queue_wait_ms span.
    """
    best_trace = None
    best_wait = 0.0

    print(f"Evaluating {len(traces)} candidate traces...")
    for candidate in traces:
        trace_id = candidate.get("traceID") or candidate.get("traceId", "")
        if not trace_id:
            continue
        try:
            detail = get_trace_detail(tempo_url, trace_id)
            wait_ms = extract_queue_wait_ms(detail)
            print(f"  {trace_id[:16]}...  queue_wait_ms={wait_ms:.0f}")
            if wait_ms > best_wait:
                best_wait = wait_ms
                best_trace = detail
                best_trace["_traceID"] = trace_id
        except Exception as e:
            print(f"  {trace_id[:16]}... error: {e}")

    return best_trace, best_wait


def main():
    parser = argparse.ArgumentParser(description="Find hero trace for demo Tab 3")
    parser.add_argument("--manifest", default="run_manifest.json")
    parser.add_argument("--tempo-url", default="http://localhost:3200")
    parser.add_argument("--grafana-url", default="http://localhost:3001")
    parser.add_argument("--window-start", help="Override: HH:MM:SS UTC")
    parser.add_argument("--window-end",   help="Override: HH:MM:SS UTC")
    args = parser.parse_args()

    # Determine search window
    if args.window_start and args.window_end:
        today = datetime.now(timezone.utc).date()
        fmt = "%H:%M:%S"
        window_start = datetime.strptime(args.window_start, fmt).replace(
            year=today.year, month=today.month, day=today.day,
            tzinfo=timezone.utc
        )
        window_end = datetime.strptime(args.window_end, fmt).replace(
            year=today.year, month=today.month, day=today.day,
            tzinfo=timezone.utc
        )
    else:
        manifest = load_manifest(Path(args.manifest))
        window_start, window_end = get_burst_window(manifest)

    print(f"\nSearching Tempo for traces between:")
    print(f"  From: {window_start.strftime('%H:%M:%S')} UTC")
    print(f"  To:   {window_end.strftime('%H:%M:%S')} UTC\n")

    # Query Tempo
    candidates = query_tempo(args.tempo_url, window_start, window_end)
    if not candidates:
        print("No traces found in window. Check that the slow phase ran correctly.")
        sys.exit(1)

    hero_trace, queue_wait_ms = find_best_trace(candidates, args.tempo_url)
    if not hero_trace:
        print("Could not find a trace with queue_wait_ms attribute.")
        print("Check that your demo app is emitting this span attribute.")
        sys.exit(1)

    trace_id = hero_trace["_traceID"]

    # Write hero trace file
    Path("hero_trace.json").write_text(json.dumps(hero_trace, indent=2))

    # Print results
    print(f"\n{'═' * 60}")
    print(f"  HERO TRACE FOUND")
    print(f"{'═' * 60}")
    print(f"  Trace ID:       {trace_id}")
    print(f"  Queue wait:     {queue_wait_ms:.0f} ms  ({queue_wait_ms/1000:.1f}s)")
    print(f"\n  Tempo deep link:")
    print(f"  {args.grafana_url}/explore?")
    print(f"    datasource=tempo&")
    print(f"    left={{\"queries\":[{{\"query\":\"{trace_id}\"}}]}}")
    print(f"\n  Bookmark this URL for demo Tab 3.")
    print(f"  Full trace written to: hero_trace.json")
    print(f"{'═' * 60}\n")


if __name__ == "__main__":
    main()
