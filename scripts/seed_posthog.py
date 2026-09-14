"""Push deterministic synthetic events to PostHog (docs/06) and verify.

``--verify`` runs each seeded bet's HogQL query and prints expected vs. actual
(exit 1 on mismatch). Re-running keeps lifts correct: all counts scale, rates
(and therefore lifts) are preserved — idempotent-ish per docs/06.
"""

from __future__ import annotations

import argparse
import sys
from datetime import timedelta

import httpx
from seed_demo import D1, D1_SHIP, S1_SHIP, S2_SHIP, S3_SHIP, SEEDS  # shared spec + HogQL queries

from pm_agent.config import get_settings
from pm_agent.integrations.posthog import run_hogql

SHIP_BY_KEY = {"S1": S1_SHIP, "S2": S2_SHIP, "S3": S3_SHIP, "D1": D1_SHIP}
EXPECTED_LIFT = {"S1": 12.0, "S2": 1.0, "S3": 9.9, "D1": 6.0}

# (event, count, cohort) per bet — deterministic counts producing exact lifts.
EVENTS: dict[str, list[tuple[str, int, str]]] = {
    "S1": [
        ("checkout_started", 100, "before"),
        ("checkout_completed", 50, "before"),
        ("checkout_started", 100, "after"),
        ("checkout_completed", 62, "after"),
    ],
    "S2": [
        ("signup_started", 100, "before"),
        ("signup_activated", 50, "before"),
        ("signup_started", 100, "after"),
        ("signup_activated", 51, "after"),
    ],
    "S3": [
        ("signup_started", 1000, "before"),
        ("retained_7d", 300, "before"),
        ("signup_started", 1000, "after"),
        ("retained_7d", 399, "after"),
    ],
    "D1": [
        ("trial_started", 100, "before"),
        ("upgraded", 12, "before"),
        ("trial_started", 100, "after"),
        ("upgraded", 18, "after"),
    ],
}


def _events_payload() -> list[dict]:
    events: list[dict] = []
    for key, entries in EVENTS.items():
        ship = SHIP_BY_KEY[key]
        for event, count, cohort in entries:
            timestamp = (
                ship - timedelta(days=1) if cohort == "before" else ship + timedelta(days=1)
            ).isoformat()
            for i in range(count):
                events.append(
                    {
                        "event": event,
                        "distinct_id": f"seed-{key}-{cohort}-{i}",
                        "properties": {"seed": key},
                        "timestamp": timestamp,
                    }
                )
    return events


def _push_events(events: list[dict]) -> None:
    import os
    from pathlib import Path

    settings = get_settings()
    api_key = os.environ.get("POSTHOG_PROJECT_API_KEY", "")
    if not api_key:
        env_file = Path(".env")
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("POSTHOG_PROJECT_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not api_key:
        print("[posthog] error: POSTHOG_PROJECT_API_KEY missing from .env (project key phc_...)")
        raise SystemExit(1)
    url = f"{settings.posthog_host}/capture/"
    with httpx.Client(timeout=60) as client:
        for start in range(0, len(events), 200):
            chunk = events[start : start + 200]
            response = client.post(url, json={"api_key": api_key, "batch": chunk})
            if response.status_code >= 400:
                print(
                    f"[posthog] error: capture returned {response.status_code}: "
                    f"{response.text[:200]}"
                )
                raise SystemExit(1)
    print(f"[posthog] pushed {len(events)} events in batches of 200")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed PostHog events (docs/06)")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="run the seeded HogQL queries and print expected vs. actual",
    )
    parser.add_argument(
        "--skip-push", action="store_true", help="only run --verify (no event push)"
    )
    args = parser.parse_args()

    if not args.skip_push:
        _push_events(_events_payload())

    if not args.verify:
        return 0

    settings = get_settings()
    problems: list[str] = []
    for spec in [*SEEDS, D1]:
        key = spec["key"]
        result = run_hogql(settings, spec["metric_query"])
        expected = EXPECTED_LIFT[key]
        if result.error is not None:
            print(f"[verify] {key}: ERROR {result.error.message}")
            problems.append(f"{key}: {result.error.message}")
            continue
        actual = float(result.value)
        status = "OK" if abs(actual - expected) < 0.001 else "MISMATCH"
        print(f"[verify] {key}: expected {expected}, actual {actual} -> {status}")
        if status != "OK":
            problems.append(f"{key}: {actual} != {expected}")

    if problems:
        print("[verify] FAILED — re-run seed_posthog.py or check the PostHog data")
        return 1
    print("[verify] all seeded lifts match")
    return 0


if __name__ == "__main__":
    sys.exit(main())
