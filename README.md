# Wager

**Turn product meetings into tracked, graded bets.**

PMs make predictions in every planning meeting — "this will lift completion
15%". Nobody writes them down, and nobody checks them later. Wager is an
agent that does both: it reads the meeting transcript, extracts the team's
reasoning into a queryable decision graph, gets the PM to attest the decision
in Slack, tracks the bet in GitHub and Google Calendar — and when the review
date arrives, it queries PostHog and grades the prediction. Over time it
computes a **PM calibration score**: how good is this team at predicting its
own impact?

## The loop

```
Meeting transcript (Google Docs)
  → extract FACTS (what was actually said, attributed per speaker)
  → derive ASSUMPTIONS, HYPOTHESES, OPTIONS (traceable back to the room)
  → PM confirms the chosen option in Slack   ← human attestation, not LLM guess
  → GitHub issue + Calendar review event
        …weeks pass…
  → review fires → query PostHog for the actual metric
  → grade: supports / contradicts (|delta| ≤ 5pp tolerance)
  → Slack report: result, updated calibration, which assumption broke
```

Every assumption in the graph connects via `derived_from` edges to quotes from
the actual meeting — you can always ask *why did we believe this?* and walk
back to what was said in the room.

## Architecture

- **LangGraph** pipeline with a human-in-the-loop interrupt (Slack button →
  graph resume), persisted via a Postgres checkpointer (`thread_id = bet_id`)
- **PostgreSQL is the single source of truth.** GitHub, Calendar, Slack are
  side effects; every external failure surfaces as a typed, persisted
  `DataFlag` rather than a crash or a silent skip (**fail-visible** design)
- **Deterministic grading:** delta computation, tolerance checks and
  calibration are plain SQL/Python — LLMs only do language understanding
  (structured JSON output, schema-validated)
- FastAPI + CLI interfaces

External services: Google Docs, Slack (two-way), GitHub, Google Calendar,
PostHog.

## Quickstart

```bash
pip install -e ".[dev]"
# .env with credentials (see .env.example)
python scripts/migrate.py
uvicorn pm_agent.api.app:app --reload
```

CLI:

```bash
python -m pm_agent.cli run-phase1                  # ingest a meeting doc
python -m pm_agent.cli confirm <bet_id> --option 1 # PM confirms (if Slack is down)
python -m pm_agent.cli check-due-reviews           # grade due bets vs PostHog
python -m pm_agent.cli show-bet <bet_id>           # inspect the reasoning graph
python -m pm_agent.cli calibration                 # PM's running calibration score
```

## Human-in-the-loop by design

The DECISION node — the accountability record — is written **only** after the
PM clicks a Slack button. The LLM proposes; the human disposes. A bet that
never gets confirmed stays visibly `PENDING_CONFIRMATION`. An accountability
tool whose bookkeeping is itself probabilistic would be self-defeating.

## Reliability

Postgres-first writes, typed failure flags rendered verbatim in reports,
idempotent review checks, resumable LangGraph checkpoints. See the project
documentation for the full reliability brief and architecture decision records.
