"""PM calibration aggregates over the outcomes table (pure SQL, deterministic)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Calibration:
    mean_abs_delta: float | None
    hit_rate: float | None
    n_resolved: int


def compute_calibration(conn) -> Calibration:
    """Compute mean |delta| and within-tolerance hit rate over all outcomes."""
    with conn.cursor() as cur:
        cur.execute("SELECT avg(abs(delta)), avg(within_tolerance::int), count(*) FROM outcomes")
        mean_abs_delta, hit_rate, n_resolved = cur.fetchone()
    if n_resolved == 0:
        return Calibration(None, None, 0)
    return Calibration(float(mean_abs_delta), float(hit_rate), int(n_resolved))
