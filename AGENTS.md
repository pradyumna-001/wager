# AGENTS.md — PM Decision-Tracking Agent

> **This file is the entry point for every coding agent working on this repo.**
> Read it fully before writing any code. Then read the docs your issue points to.

## What this project is

A hackathon build (today, one day): an AI agent that reads product-planning
meeting transcripts, extracts the PM's reasoning into a queryable decision graph
(facts → assumptions → hypotheses → options → decisions), gets the PM to confirm
the decision via Slack, tracks the bet in GitHub + Google Calendar, and weeks
later queries PostHog to grade the prediction — producing a PM calibration score.

Full product context: `docs/00_PRODUCT_BRIEF.md`
Original design doc (superseded by `docs/` where they differ): `PM_DECISION_AGENT_DESIGN.md`

## Non-negotiable invariants (never violate these)

1. **Postgres is the only source of truth.** GitHub, Calendar, Slack, Sheets are
   side effects. A graph record is written to Postgres BEFORE any external write.
   A failed external write appends to `state["flags"]` and is surfaced in the
   report — it never crashes the pipeline and never loses a decision.
2. **DECISION nodes are human-attested.** The LLM only *proposes* the chosen
   option (`proposed_option_id`). A DECISION node exists only after the PM
   clicks a Slack button. No exception, no shortcut in seeds (seeds write the
   node directly but are clearly synthetic data, not pipeline output).
3. **Fail-visible, never silent.** Integration functions return
   `FetchResult(value, error: DataFlag)` — never raise on external failure.
   Nodes convert `result.error` into an appended flag; report templates render
   flags verbatim. Flags are persisted to `bets.flags` (JSONB). See ADR 0001/0005.
4. **One bet per Phase 1 run.** Do not build multi-bet transcript segmentation.
5. **Deterministic where possible.** `compare_outcome`, `update_calibration`,
   edge construction, and SQL writes are plain Python/SQL — no LLM calls.

## Coding conventions

- Python 3.11+, type hints everywhere, Pydantic v2 for all schemas.
- Package layout under `src/pm_agent/` (see `docs/01_ARCHITECTURE.md` §Layout).
- One LangGraph node function per integration/LLM step; nodes are thin: they
  call a function in `integrations/` or `graph/` and update state.
- LLM calls go ONLY through `src/pm_agent/llm.py::call_llm` (Structured JSON
  output). Never instantiate a model client inside a pipeline node.
- Every pipeline node and integration function has a unit test. LLM and all
  external APIs are mocked in tests (no network in `pytest`).
- Config only via `src/pm_agent/config.py` (pydantic-settings). Never read
  `os.environ` outside `config.py`. Access settings via `get_settings()`
  (`@lru_cache`) called INSIDE functions — never a module-level
  `settings = Settings()` at import time (breaks test isolation).
- All external failures → typed `DataFlag` (models.py, `Source`/`Severity`
  enums). No ad-hoc flag strings anywhere.
- Nodes are registered via `add_node_checked(graph, name, fn)` (nodeutil.py) so
  logging/context/error translation is uniform.
- SQL migrations live in `src/pm_agent/db/migrations/` as numbered `.sql` files,
  applied by `scripts/migrate.py`.
- Format: `ruff format` + `ruff check`. Line length 100.

## Golden file map (consult before touching)

| You are changing… | Read first |
|---|---|
| Node/edge schema, BetState, SQL | `docs/02_DATA_MODEL.md` |
| Any LangGraph step, prompts, phase flow | `docs/03_PIPELINE_SPEC.md` |
| Slack/GitHub/GCal/GDocs/PostHog calls | `docs/04_INTEGRATIONS.md` |
| FastAPI endpoints, webhook payloads | `docs/05_API_SPEC.md` |
| Seed data, demo fixtures | `docs/06_SEED_AND_DEMO_DATA.md` |
| Reliability invariants, failure modes | `docs/07_RELIABILITY_BRIEF.md` |

## Commands

```bash
pip install -e ".[dev]"
python scripts/migrate.py            # apply SQL migrations
pytest                               # all tests (no network)
uvicorn pm_agent.api.app:app --reload
python -m pm_agent.cli run-phase1    # CLI entrypoints (see docs/05_API_SPEC.md §CLI)
python -m pm_agent.cli check-due-reviews
```

## Working rules for coding agents

- Work on ONE issue from `docs/issues/` at a time; issues declare their
  dependencies — respect them. Read `docs/09_MILESTONES.md` for the order.
- Do not refactor code outside your issue's declared file scope.
- Match spec signatures EXACTLY. If a spec seems wrong, add a `# SPEC-QUESTION:`
  comment and continue with the documented behavior — do not silently deviate.
- Acceptance criteria in each issue are the definition of done. Run the
  verification command listed in the issue before marking complete.
