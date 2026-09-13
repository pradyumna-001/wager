"""Dev tool: run Phase 1 extraction on the sample transcript with the REAL LLM.

Not a test (LLM nondeterminism, run manually). Creates a throwaway bets row,
runs extract_facts + derive_reasoning, and prints a diff vs the expected
fixture. Tune prompts.py until the diff is empty or trivially small.

Requires .env with DATABASE_URL + LLM credentials. Exit code 0 when the
extraction matches the fixture (or documented equivalent), 1 on gross mismatch.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

from pm_agent.db.connection import get_conn
from pm_agent.graph import store
from pm_agent.pipeline.nodes.extraction import derive_reasoning, extract_facts
from pm_agent.state import create_initial_state

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
MEETING_DATE = "2026-09-13"
LIFT_TOLERANCE_PP = 3.0


def main() -> int:
    transcript = (FIXTURES / "sample_transcript.md").read_text(encoding="utf-8")
    expected = json.loads((FIXTURES / "expected_extraction.json").read_text(encoding="utf-8"))

    bet_id = str(uuid.uuid4())
    with get_conn() as conn:
        store.create_bet(conn, bet_id, "dev-transcript-doc", MEETING_DATE, "U000TEST")
    state = create_initial_state(bet_id, "dev-transcript-doc", MEETING_DATE)
    state["raw_transcript"] = transcript

    print(f"[dev] throwaway bet {bet_id} (left in DB for inspection)")
    facts_delta = extract_facts(state)
    state["facts"] = facts_delta["facts"]
    derive_delta = derive_reasoning(state)

    problems: list[str] = []

    print(f"\n=== FACTS: got {len(state['facts'])}, expected {len(expected['facts'])} ===")
    if len(state["facts"]) != len(expected["facts"]):
        problems.append(f"facts count {len(state['facts'])} != {len(expected['facts'])}")
    for i, node in enumerate(state["facts"]):
        speaker = node.source.split("speaker=")[-1]
        print(f"  [{i}] ({speaker}) {node.content}")
    if len(state["facts"]) == len(expected["facts"]):
        for i, (node, exp) in enumerate(zip(state["facts"], expected["facts"])):
            if exp["speaker"].lower() not in node.source.lower():
                problems.append(f"fact {i} speaker mismatch: expected {exp['speaker']}")

    assumptions = derive_delta["assumptions"]
    print(f"\n=== ASSUMPTIONS: got {len(assumptions)}, expected {len(expected['assumptions'])} ===")
    if len(assumptions) != len(expected["assumptions"]):
        problems.append(f"assumptions count {len(assumptions)} != {len(expected['assumptions'])}")
    for i, node in enumerate(assumptions):
        print(f"  [{i}] {node.content}")

    hypotheses = derive_delta["hypotheses"]
    print(f"\n=== HYPOTHESES: got {len(hypotheses)}, expected 1 ===")
    if len(hypotheses) != 1:
        problems.append(f"hypotheses count {len(hypotheses)} != 1")
    expected_hyp = expected["hypotheses"][0]
    if hypotheses:
        hyp = hypotheses[0]
        print(f"  content: {hyp.content}")
        print(f"  metric_name: {hyp.metric_name} (expected {expected_hyp['metric_name']})")
        print(f"  predicted_lift: {hyp.predicted_lift} (expected {expected_hyp['predicted_lift']})")
        print(
            f"  measurement_days: {hyp.measurement_days}"
            f" (expected {expected_hyp['measurement_days']})"
        )
        print(f"  metric_query: {hyp.metric_query}")
        if hyp.metric_name != expected_hyp["metric_name"]:
            problems.append(f"metric_name {hyp.metric_name!r} != {expected_hyp['metric_name']!r}")
        if abs(hyp.predicted_lift - expected_hyp["predicted_lift"]) > LIFT_TOLERANCE_PP:
            problems.append(f"predicted_lift {hyp.predicted_lift} too far from 15.0")
        if hyp.measurement_days != expected_hyp["measurement_days"]:
            problems.append(f"measurement_days {hyp.measurement_days} != 14")

    options = derive_delta["options"]
    print(f"\n=== OPTIONS: got {len(options)}, expected {len(expected['options'])} ===")
    if len(options) != len(expected["options"]):
        problems.append(f"options count {len(options)} != {len(expected['options'])}")
    for i, node in enumerate(options):
        print(f"  [{i}] {node.content}")
    got_index = None
    if derive_delta["proposed_option_id"]:
        for i, node in enumerate(options):
            if str(node.id) == derive_delta["proposed_option_id"]:
                got_index = i
    print(
        f"  proposed: {got_index if got_index is not None else 'none'}"
        f" (expected {expected['proposed_option_index']})"
    )
    if got_index != expected["proposed_option_index"]:
        problems.append("proposed option does not match expected (option A)")

    for flag in derive_delta["flags"]:
        print(f"\n  flag: {flag.message}")
        problems.append(f"unexpected flag: {flag.message}")

    print("\n=== DIFF SUMMARY ===")
    if problems:
        for p in problems:
            print(f"  MISMATCH: {p}")
        print("  -> tune prompts.py until the diff is empty or trivially small")
        return 1
    print("  extraction matches the fixture (or documented equivalent)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
