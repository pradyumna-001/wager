"""CLI tests (minimal, mocked services; full CLI tests are a follow-up)."""

from __future__ import annotations

from contextlib import contextmanager

from pm_agent.cli import app as typer_app


def test_check_due_reviews(monkeypatch):
    from typer.testing import CliRunner

    import pm_agent.cli as cli

    monkeypatch.setattr(
        cli,
        "run_due_reviews",
        lambda: {"checked": 1, "resolved": 1, "flagged": 0},
    )

    result = CliRunner().invoke(typer_app, ["check-due-reviews"])

    assert result.exit_code == 0
    assert "checked: 1, resolved: 1, flagged: 0" in result.output


def test_calibration(monkeypatch):
    from typer.testing import CliRunner

    import pm_agent.cli as cli
    from pm_agent.graph.calibration import Calibration

    @contextmanager
    def fake_conn():
        yield object()

    monkeypatch.setattr(cli, "get_conn", fake_conn)
    monkeypatch.setattr(cli, "compute_calibration", lambda conn: Calibration(7.2, 0.5, 4))

    result = CliRunner().invoke(typer_app, ["calibration"])

    assert result.exit_code == 0
    assert "4 resolved bets" in result.output
    assert "7.2" in result.output
