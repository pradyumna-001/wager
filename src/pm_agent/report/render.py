"""Deterministic report rendering (docs/03 §report_outcome, ADR 0001).

No LLM: templates + flag rendering live here, fully unit-testable. ``bet``,
``outcome`` and ``calibration`` are duck-typed (their models land with
M1.2/M1.3/M5.2); the attribute contract is documented on ``build_report_text``.
"""

from pm_agent.models import DataFlag

_TIMEOUT_MESSAGE = "posthog_timeout"


def flag_lines(flags: list[DataFlag]) -> list[str]:
    """Render each DataFlag as ``⚠️ {source}: {message}`` verbatim."""
    return [f"⚠️ {f.source.value}: {f.message}" for f in flags]


def build_report_text(bet, outcome, calibration, flags: list[DataFlag]) -> str:
    """Build the Slack report text per docs/03 §report_outcome.

    Duck-typed inputs (models land with M1.2/M1.3/M5.2):
    - ``bet``: ``.hypothesis: str``, ``.metric_name: str``,
      ``.predicted_lift: float``, ``.window: str``
    - ``outcome``: ``.actual_lift: float | None``, ``.delta: float | None``,
      ``.supports: bool | None``, ``.tolerance: float``,
      ``.assumption_content: str`` (weakest/key assumption content, precomputed
      per docs/03's depends_on traversal)
    - ``calibration``: ``.mean_abs_delta: float | None``, ``.n_resolved: int``,
      ``.hit_rate: float`` (fraction per docs/06)

    A ``posthog_timeout`` flag replaces the Predicted/Actual result lines with
    the failure line (the report is still sent). If ``flags`` is non-empty,
    each flag renders as a ``⚠️ {source}: {message}`` line appended at the end.
    """
    lines: list[str] = [f'📊 Bet resolved: "{bet.hypothesis}"']
    if any(f.message == _TIMEOUT_MESSAGE for f in flags):
        lines.append(
            "  Could not fetch metric from PostHog (timeout). Will retry on next review check."
        )
    else:
        verdict = "✅ supports" if outcome.supports else "❌ contradicts"
        lines.append(f"  Predicted: +{bet.predicted_lift}pp {bet.metric_name} in {bet.window}")
        lines.append(
            f"  Actual:    {outcome.actual_lift:+}pp → delta {outcome.delta:+}pp"
            f" → {verdict} (tolerance ±{outcome.tolerance}pp)"
        )
    if calibration.mean_abs_delta is None or not calibration.n_resolved:
        lines.append("  PM calibration: no resolved bets yet")
    else:
        lines.append(
            f"  PM calibration: mean |delta| = {calibration.mean_abs_delta:.1f}pp"
            f" across {calibration.n_resolved} resolved bets,"
            f" {round(calibration.hit_rate * 100)}% within tolerance"
        )
    if outcome.supports:
        lines.append(f"  Key assumption held: {outcome.assumption_content}")
    else:
        lines.append(f"  Weakest assumption: {outcome.assumption_content}")
    lines.extend(flag_lines(flags))
    return "\n".join(lines)
