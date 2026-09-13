"""CLI entrypoints (docs/05 §CLI). Mirrors the API; used in the demo."""

from __future__ import annotations

import uuid
from datetime import date

import typer
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command

from pm_agent.config import get_settings
from pm_agent.db.connection import get_conn
from pm_agent.errors import EmptyExtractionError, IntegrationError
from pm_agent.graph import store
from pm_agent.graph.calibration import compute_calibration
from pm_agent.pipeline.phase1 import build_phase1_graph
from pm_agent.pipeline.phase2 import run_due_reviews
from pm_agent.state import create_initial_state

app = typer.Typer(help="PM decision-tracking agent")

_NODE_TYPES = ("FACT", "ASSUMPTION", "HYPOTHESIS", "OPTION", "DECISION")


def _make_saver() -> PostgresSaver:
    return PostgresSaver.from_conn_string(get_settings().database_url)


@app.command()
def run_phase1(doc_id: str = typer.Option(None, "--doc-id")) -> None:
    """Run Phase 1 up to the interrupt (creates the bets row)."""
    settings = get_settings()
    doc_id = doc_id or settings.google_doc_id
    bet_id = str(uuid.uuid4())
    with get_conn() as conn:
        store.create_bet(conn, bet_id, doc_id, date.today().isoformat(), settings.pm_slack_user_id)
    with _make_saver() as saver:
        saver.setup()
        graph = build_phase1_graph(saver)
        state = create_initial_state(bet_id, doc_id, date.today().isoformat())
        try:
            result = graph.invoke(state, {"configurable": {"thread_id": bet_id}})
        except EmptyExtractionError:
            typer.echo("extraction produced no facts", err=True)
            raise typer.Exit(1)
        except IntegrationError as exc:
            typer.echo(f"phase1 failed: {exc}", err=True)
            raise typer.Exit(1)
    if "__interrupt__" in result:
        value = result["__interrupt__"][0].value
        typer.echo(f"bet {bet_id} PENDING_CONFIRMATION")
        for i, option in enumerate(value["options"]):
            marker = "*" if i == value["proposed_index"] else " "
            typer.echo(f"  {marker} ({i}) {option}")
        typer.echo(f"confirm with: python -m pm_agent.cli confirm {bet_id} --option <i>")


@app.command()
def confirm(bet_id: str, option: int = typer.Option(..., "--option")) -> None:
    """Resume Phase 1 directly (no Slack; for testing)."""
    get_settings()
    with _make_saver() as saver:
        saver.setup()
        graph = build_phase1_graph(saver)
        try:
            result = graph.invoke(
                Command(resume={"chosen_option_index": option}),
                {"configurable": {"thread_id": bet_id}},
            )
        except Exception as exc:
            typer.echo(f"confirm failed: {exc}", err=True)
            raise typer.Exit(1)
    typer.echo(f"✅ Recorded. Bet tracked — I'll come back on {result.get('review_date')}")


@app.command("check-due-reviews")
def check_due_reviews() -> None:
    """Run Phase 2 for all due bets."""
    summary = run_due_reviews()
    typer.echo(
        f"checked: {summary['checked']}, resolved: {summary['resolved']},"
        f" flagged: {summary['flagged']}"
    )


@app.command("show-bet")
def show_bet(bet_id: str) -> None:
    """Pretty-print the bet's graph adjacency (demo)."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, status, metric_name, predicted_lift, review_date"
                " FROM bets WHERE id = %s",
                (bet_id,),
            )
            row = cur.fetchone()
        if row is None:
            typer.echo(f"bet {bet_id} not found", err=True)
            raise typer.Exit(1)
        nodes, edges = store.get_bet_graph(conn, bet_id)
    typer.echo(f"bet {row[0]} [{row[1]}] metric={row[2]} predicted={row[3]} review={row[4]}")
    by_id = {str(n.id): n for n in nodes}
    for node_type in _NODE_TYPES:
        group = [n for n in nodes if str(n.type) == node_type]
        if not group:
            continue
        typer.echo(f"{node_type}:")
        for node in group:
            typer.echo(f'  - "{node.content}" [{node.source}]')
            if node_type == "FACT":
                continue
            for edge in edges:
                if str(edge.from_node) == str(node.id) and str(edge.edge_type) == "derived_from":
                    target = by_id.get(str(edge.to_node))
                    if target:
                        typer.echo(f'      ↳ derived_from: "{target.content}" [{target.source}]')


@app.command()
def calibration() -> None:
    """Print the PM calibration score."""
    with get_conn() as conn:
        cal = compute_calibration(conn)
    tol = get_settings().bet_tolerance_pp
    typer.echo(
        f"mean |delta| = {cal.mean_abs_delta}pp across {cal.n_resolved} resolved bets,"
        f" hit_rate={cal.hit_rate}, tolerance ±{tol}pp"
    )


if __name__ == "__main__":
    app()
