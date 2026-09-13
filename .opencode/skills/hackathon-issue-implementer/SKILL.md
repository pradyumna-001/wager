---
name: hackathon-issue-implementer
description: Use when implementing one issue from docs/issues/ of the PM Decision-Tracking Agent hackathon repo. Enforces the plan-first workflow: (1) write a solution plan and WAIT for user approval, (2) implement only after explicit approval, (3) commit to a dedicated branch and push — never merge; the user merges. Also mandates consulting the FinAgent reference repo when an architectural question arises. Do NOT use for free-form questions about the project or for documentation edits.
---

# Hackathon Issue Implementer

The one-day build discipline for this repo. Three phases: **plan → approve →
build & push**. You never merge. The user merges.

## Phase 0 — Orient (before any plan)

1. Read `AGENTS.md` in full. Non-negotiable. Pay special attention to the five
   invariants:
   - Postgres is the only source of truth; external apps are side effects.
   - DECISION nodes are human-attested (no LLM-autonomous DECISION writes).
   - Fail-visible: integrations return `FetchResult(value, error: DataFlag)`.
   - One bet per Phase 1 run.
   - Grading/calibration are deterministic code, no LLM.
2. Read the issue file in `docs/issues/` you were assigned — and ONLY that issue.
3. Read every doc the issue lists under "Read first", plus the golden-file map
   in `AGENTS.md` for any file you expect to touch.
4. Verify the issue's dependencies are met (code exists or prior issue merged).
   If not: STOP and report "blocked on issue X" — do not build the dependency
   yourself.

## Phase 1 — Solution plan (REQUIRED, blocking)

Write a plan and wait for explicit approval. Format:

```
## Plan: <issue-id> — <title>

**Goal (1 line)**

**Files to create/modify** — every path, one line each, and why.

**Key decisions** — 2–5 bullets: signatures chosen, where invariants are enforced,
what gets mocked in tests. Flag anything the spec is silent on and your chosen
default ("SPEC-GAP: …; defaulting to …").

**Test plan** — the tests you will write, one line each mapping to an
acceptance criterion.

**Verification command** — copied verbatim from the issue.

**Open questions** — only if a real ambiguity blocks a good default; otherwise "none".
```

Hard stop: **do not write any code until the user replies with explicit approval**
("approved", "go", "LGTM", etc.). A follow-up question from the user is NOT
approval; answer it and re-present the updated plan.

If the plan changes mid-implementation (discovered constraint), stop, present a
plan delta, and wait for approval again.

## FinAgent consultation rule

A reference implementation exists at:
`C:\Users\mayco\OneDrive\Documents\finAgent`

Consult it when you face an architectural question the docs don't answer
(e.g., checkpointer wiring, DataFlag patterns, testcontainers setup, API error
translation, contextvar logging). Rules:

- **Consult, don't copy.** FinAgent is the *warning-and-pattern* library: copy
  its good patterns (ADRs 0001–0006 list which ones), and know its known
  anti-patterns (hand-rolled LLM JSON validation, module-level settings reads,
  import-time graph construction, duplicated SSE/log boilerplate in nodes).
- **Cite it in the plan.** If your plan borrows from FinAgent, say which
  file/ADR you modeled it on (e.g., "modeled on finAgent `app/services/tavily.py`
  result-object pattern, minus the module-level settings read").
- **Never `pip install` from it, import from it, or add it as a dependency.**
  Read-only reference.
- If FinAgent and this repo's docs disagree, **this repo's docs win.** Note the
  conflict with a `# SPEC-QUESTION:` comment in code.

## Phase 2 — Implement (only after approval)

1. **Branch first, before any file changes:**
   ```bash
   git checkout -b <scope>/issue-<ID>-<short-slug>
   ```
   Scope: `feature/`, `fix/`, `refactor/`, `docs/`, `chore/`, `test/`.
   Example: `feature/issue-M3.5-phase1-graph`. Never commit to `main`.
2. Implement within the issue's declared file scope ONLY. Out-of-scope edits are
   forbidden — if you discover a bug elsewhere, report it, don't fix it.
3. Follow `AGENTS.md` conventions: `get_settings()` via lru_cache inside
   functions (never module-level), `add_node_checked` for graph nodes, typed
   `DataFlag`s, no network in tests, type hints everywhere, ruff line-length 100.
4. Match spec signatures EXACTLY. If a spec seems wrong, add `# SPEC-QUESTION:`
   and continue with documented behavior.
5. Commit per logical chunk (Conventional Commits: `<type>(<scope>): <summary>`),
   so the branch tells a story. Rough guide: 2–8 commits per issue.

## Phase 3 — Verify & push (then STOP)

1. Run the issue's verification command. All green — fix until green; do not
   push red.
2. Also run: `pytest -q` (full suite, no regressions) and `ruff check src tests`.
3. Push: `git push -u origin <branch>`.
4. Report back:
   - branch name + commit log,
   - verification output (or summary),
   - which acceptance criteria are met (checklist),
   - any `# SPEC-QUESTION`s, plan deltas, or out-of-scope bugs found.
5. **Then STOP. Never merge, never open a PR unless explicitly asked.** The user
   reviews and merges.

## Repo notes

- **This repo's `docs/` and `.env` are gitignored by design** — the planning docs
  and secrets are local-only; code goes to GitHub, plans stay local. All specs
  still live at `docs/` in the working tree and remain your source of truth.
  NEVER force-add `docs/`, `.env`, or `secrets/` to git.
- `AGENTS.md` at the repo root is committed and is every agent's entry point.

## Hard rules (never violate)

1. No code before plan approval.
2. No commits to `main`; branch-per-issue; push at end.
3. No merges, no `gh pr create`, no force-push, no `git reset`/`rebase` on shared
   history without explicit instruction.
4. No out-of-scope file edits.
5. No network calls in tests (all integrations mocked; testcontainers Postgres
   is the only allowed live dependency in integration tests).
6. No silent spec deviations.
7. If blocked (dependency missing, spec contradiction, approval not given):
   report and stop — do not improvise around it.
