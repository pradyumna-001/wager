"""Golden-string tests for report rendering (docs/03 §report_outcome, ADR 0001)."""

from types import SimpleNamespace

from pm_agent.models import DataFlag, Severity, Source
from pm_agent.report.render import build_report_text, flag_lines


def _bet() -> SimpleNamespace:
    return SimpleNamespace(
        hypothesis="Search drives signup activation",
        metric_name="signup_completion_rate",
        predicted_lift=15.0,
        window="14 days",
    )


def _calibration() -> SimpleNamespace:
    return SimpleNamespace(mean_abs_delta=4.6333, n_resolved=3, hit_rate=2 / 3)


SUPPORTS_GOLDEN = (
    '📊 Bet resolved: "Search drives signup activation"\n'
    "  Predicted: +15.0pp signup_completion_rate in 14 days\n"
    "  Actual:    +17.0pp → delta +2.0pp → ✅ supports (tolerance ±5.0pp)\n"
    "  PM calibration: mean |delta| = 4.6pp across 3 resolved bets, 67% within tolerance\n"
    "  Key assumption held: Users want faster onboarding"
)

CONTRADICTS_GOLDEN = (
    '📊 Bet resolved: "Search drives signup activation"\n'
    "  Predicted: +15.0pp signup_completion_rate in 14 days\n"
    "  Actual:    -7.0pp → delta -22.0pp → ❌ contradicts (tolerance ±5.0pp)\n"
    "  PM calibration: mean |delta| = 4.6pp across 3 resolved bets, 67% within tolerance\n"
    "  Weakest assumption: Users want faster onboarding"
)

TIMEOUT_GOLDEN = (
    '📊 Bet resolved: "Search drives signup activation"\n'
    "  Could not fetch metric from PostHog (timeout). Will retry on next review check.\n"
    "  PM calibration: mean |delta| = 4.6pp across 3 resolved bets, 67% within tolerance\n"
    "  Weakest assumption: Users want faster onboarding\n"
    "⚠️ posthog: posthog_timeout"
)


def test_build_report_text_supports() -> None:
    outcome = SimpleNamespace(
        actual_lift=17.0,
        delta=2.0,
        supports=True,
        tolerance=5.0,
        assumption_content="Users want faster onboarding",
    )

    text = build_report_text(_bet(), outcome, _calibration(), [])

    assert text == SUPPORTS_GOLDEN


def test_build_report_text_contradicts() -> None:
    outcome = SimpleNamespace(
        actual_lift=-7.0,
        delta=-22.0,
        supports=False,
        tolerance=5.0,
        assumption_content="Users want faster onboarding",
    )

    text = build_report_text(_bet(), outcome, _calibration(), [])

    assert text == CONTRADICTS_GOLDEN


def test_build_report_text_timeout() -> None:
    outcome = SimpleNamespace(
        actual_lift=None,
        delta=None,
        supports=None,
        tolerance=5.0,
        assumption_content="Users want faster onboarding",
    )
    flags = [DataFlag(Source.POSTHOG, Severity.WARNING, "posthog_timeout")]

    text = build_report_text(_bet(), outcome, _calibration(), flags)

    assert text == TIMEOUT_GOLDEN


def test_flag_lines() -> None:
    flags = [
        DataFlag(Source.GDOCS, Severity.CRITICAL, "gdocs_fetch_failed: 500"),
        DataFlag(Source.GCAL, Severity.WARNING, "calendar_write_failed: boom"),
    ]

    assert flag_lines(flags) == [
        "⚠️ gdocs: gdocs_fetch_failed: 500",
        "⚠️ gcal: calendar_write_failed: boom",
    ]
    assert flag_lines([]) == []
