"""Seed the demo bets S1-S3 + D1 into Postgres (docs/06 authoritative values).

Idempotent: skips when the seed bets already exist; ``--reset`` truncates
bets/graph_nodes/graph_edges/outcomes first. The DECISION nodes are marked
``[seed]`` — seeds bypass Slack by design (synthetic data, ADR 0002 carve-out).
"""

from __future__ import annotations

import argparse
import uuid
from datetime import date, timedelta

from pm_agent.config import get_settings
from pm_agent.db.connection import get_conn
from pm_agent.graph import store
from pm_agent.models import EdgeType, GraphEdge, GraphNode, NodeType

TODAY = date.today()
S1_SHIP = TODAY - timedelta(days=21)
S2_SHIP = TODAY - timedelta(days=21)
S3_SHIP = TODAY - timedelta(days=21)


def _lift_query(count_event: str, denom_event: str, key: str) -> str:
    """HogQL returning the pre/post lift in percentage points (2 decimals).

    Filtered to the bet's own synthetic events (``seed-{key}-%``) so bets that
    share an event name do not pollute each other's denominators.
    """
    return (
        "SELECT round(100 * ("
        "(countIf(event = '{c}' AND timestamp >= '{ship}')"
        " / countIf(event = '{d}' AND timestamp >= '{ship}'))"
        " - (countIf(event = '{c}' AND timestamp < '{ship}')"
        " / countIf(event = '{d}' AND timestamp < '{ship}'))"
        "), 2) AS lift_pp FROM events WHERE distinct_id LIKE 'seed-{key}-%'"
    ).format(c=count_event, d=denom_event, key=key, ship="{ship}")


SEEDS: list[dict] = [
    {
        "key": "S1",
        "hypothesis": "Guest checkout lifts conversion +10pp in 1 week",
        "predicted_lift": 10.0,
        "metric_name": "checkout_conversion",
        "metric_query": _lift_query("checkout_completed", "checkout_started", "S1").format(
            ship=S1_SHIP.isoformat()
        ),
        "meeting_date": S1_SHIP - timedelta(days=7),
        "review_date": S1_SHIP + timedelta(days=7),
        "facts": [
            "Guest checkout was requested by 8 of 10 enterprise prospects [seed]",
            "Checkout completion sat at 50% before the change [seed]",
        ],
        "assumptions": [
            "Forced account creation is the main checkout drop-off driver [seed]",
        ],
        "options": [
            "Ship guest checkout [seed]",
            "Keep forced signup and add reassurance copy [seed]",
        ],
    },
    {
        "key": "S2",
        "hypothesis": "Onboarding tooltip tour lifts activation +8pp in 2 weeks",
        "predicted_lift": 8.0,
        "metric_name": "signup_activation",
        "metric_query": _lift_query("signup_activated", "signup_started", "S2").format(
            ship=S2_SHIP.isoformat()
        ),
        "meeting_date": S2_SHIP - timedelta(days=7),
        "review_date": S2_SHIP + timedelta(days=14),
        "facts": [
            "Activation rate was 50% before the tooltip tour shipped [seed]",
            "Users skipped the docs page in droves [seed]",
        ],
        "assumptions": [
            "Users activate when shown the key feature early [seed]",
        ],
        "options": [
            "Ship the onboarding tooltip tour [seed]",
            "Rely on the empty-state page [seed]",
        ],
    },
    {
        "key": "S3",
        "hypothesis": "Dark mode lifts 7-day retention +5pp in 2 weeks",
        "predicted_lift": 5.0,
        "metric_name": "retention_7d",
        "metric_query": _lift_query("retained_7d", "signup_started", "S3").format(
            ship=S3_SHIP.isoformat()
        ),
        "meeting_date": S3_SHIP - timedelta(days=7),
        "review_date": S3_SHIP + timedelta(days=14),
        "facts": [
            "7-day retention was 30% before dark mode shipped [seed]",
            "Dark mode was the most upvoted community request [seed]",
        ],
        "assumptions": [
            "Comfort (eye strain) drives users away, not feature gaps [seed]",
        ],
        "options": [
            "Ship dark mode [seed]",
            "Ship a theme API instead [seed]",
        ],
    },
]

D1 = {
    "key": "D1",
    "hypothesis": "Inline upgrade prompt lifts trial-to-paid +6pp in 2 weeks",
    "predicted_lift": 6.0,
    "metric_name": "trial_to_paid",
    "metric_query": _lift_query("upgraded", "trial_started", "D1").format(
        ship=(TODAY - timedelta(days=1)).isoformat()
    ),
    "meeting_date": TODAY - timedelta(days=2),
    "review_date": TODAY + timedelta(days=1),
    "facts": [
        "Trial-to-paid sat at 12% before the prompt [seed]",
        "Competitors prompt inline at the paywall [seed]",
    ],
    "assumptions": [
        "Users convert when asked at the moment of value [seed]",
    ],
    "options": [
        "Ship the inline upgrade prompt [seed]",
        "Ship an email drip instead [seed]",
    ],
}


def _seed_bet(conn, spec: dict) -> str:
    bet_id = uuid.uuid4()
    store.create_bet(conn, bet_id, "seed", spec["meeting_date"], get_settings().pm_slack_user_id)
    facts = [
        GraphNode(bet_id=bet_id, type=NodeType.FACT, content=c, source="meeting:seed")
        for c in spec["facts"]
    ]
    assumptions = [
        GraphNode(bet_id=bet_id, type=NodeType.ASSUMPTION, content=c, source="meeting:seed")
        for c in spec["assumptions"]
    ]
    hypothesis = GraphNode(
        bet_id=bet_id,
        type=NodeType.HYPOTHESIS,
        content=spec["hypothesis"],
        confidence=0.8,
        source="meeting:seed",
    )
    options = [
        GraphNode(bet_id=bet_id, type=NodeType.OPTION, content=c, source="meeting:seed")
        for c in spec["options"]
    ]
    decision = GraphNode(
        bet_id=bet_id,
        type=NodeType.DECISION,
        content=f"[seed] Chose: {spec['options'][0]}",
        source="system",
    )
    edges: list[GraphEdge] = [
        GraphEdge(
            bet_id=bet_id, from_node=hypothesis.id, to_node=f.id, edge_type=EdgeType.DERIVED_FROM
        )
        for f in facts
    ]
    edges.extend(
        GraphEdge(
            bet_id=bet_id, from_node=hypothesis.id, to_node=a.id, edge_type=EdgeType.DEPENDS_ON
        )
        for a in assumptions
    )
    edges.extend(
        GraphEdge(
            bet_id=bet_id, from_node=options[0].id, to_node=f.id, edge_type=EdgeType.DERIVED_FROM
        )
        for f in facts
    )
    edges.append(
        GraphEdge(
            bet_id=bet_id,
            from_node=options[0].id,
            to_node=decision.id,
            edge_type=EdgeType.LEADS_TO,
        )
    )
    store.insert_nodes(conn, [*facts, *assumptions, hypothesis, *options, decision])
    store.insert_edges(conn, edges)
    store.update_bet_fields(
        conn,
        bet_id,
        metric_name=spec["metric_name"],
        metric_query=spec["metric_query"],
        predicted_lift=spec["predicted_lift"],
        review_date=spec["review_date"],
        status="SCHEDULED",
    )
    return bet_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed demo bets (docs/06)")
    parser.add_argument("--reset", action="store_true", help="truncate all tables first")
    args = parser.parse_args()

    with get_conn() as conn:
        if args.reset:
            with conn.cursor() as cur:
                for table in ("outcomes", "graph_edges", "graph_nodes", "bets"):
                    cur.execute(f"DELETE FROM {table}")
            conn.commit()
            print("[seed] reset: all tables truncated")
        else:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM bets WHERE doc_id = 'seed' LIMIT 1")
                if cur.fetchone() is not None:
                    print("[seed] seed bets already present; use --reset to reseed")
                    return 0
        conn.commit()
        for spec in [*SEEDS, D1]:
            bet_id = _seed_bet(conn, spec)
            print(f"[seed] {spec['key']}: bet {bet_id} SCHEDULED, review {spec['review_date']}")
        conn.commit()
    print(f"[seed] done — {len(SEEDS)} seeded bets + 1 demo bet (D1, review {D1['review_date']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
