# PM Decision-Tracking Agent — Graph Model & Flow

A quick naming note before anything else: this doc uses "graph node" for the
domain objects (facts, hypotheses, decisions) stored in the reasoning graph,
and "pipeline step" for the LangGraph orchestration steps. Don't let the two
"node" concepts blur together while building — they're stored and touched by
completely different code.

---

## 0. Scope decisions (locked)

- **One bet per meeting.** `extract_facts` does not try to segment a transcript
  into multiple bets. The demo meeting discusses exactly one bet; multi-bet
  segmentation is future work. This keeps `bet_id` trivially scoped: one Phase 1
  run = one `bet_id`.
- **Decision capture is human-confirmed, not LLM-inferred.** The LLM proposes
  which option the PM chose, but the DECISION node is only written after the PM
  confirms via a Slack button click (see `record_decision`). Accountability
  records must not be write-only from the LLM's guess — this is the
  reliability-critical step of the whole system.
- **Review trigger is manual for the demo.** Phase 2 is invoked via a
  `check_due_reviews()` CLI call / one API endpoint that scans `review_date <=
  now`. In production this fires from the Calendar webhook; the code path is the
  same, only the trigger changes. No polling loop is built.
- **Calibration metric is fixed:** mean absolute delta across resolved bets,
  plus hit rate at a tolerance of |delta| <= 5 percentage points.

---

## 1. Graph Data Model

### Node schema (common fields, all types)

```python
class GraphNode(BaseModel):
    id: str                    # uuid
    type: NodeType              # FACT | ASSUMPTION | HYPOTHESIS | OPTION | DECISION
    content: str                 # the actual text
    confidence: float | None      # 0.0-1.0, LLM self-rating, set on HYPOTHESIS only
    source: str                   # "meeting:<date>:speaker=<name>" or "posthog:<query_id>"
    status: NodeStatus            # ACTIVE | RESOLVED | SUPERSEDED
    created_at: datetime
    bet_id: str                   # groups all nodes belonging to one PM bet
```

`confidence` is set by `derive_reasoning` as the LLM's self-rated confidence in
each HYPOTHESIS. This enables a second calibration lens: does the LLM's
confidence predict outcomes? (Stretch goal — analysis happens offline, not in
the pipeline.)

### Node types (kept to five — enough to tell the PM story, not the full original vocabulary)

| Type | What it captures | Example |
|---|---|---|
| `FACT` | Something known/observed, not in question | "Signup funnel has 5 steps today" |
| `ASSUMPTION` | An unverified belief the bet rests on | "Users abandon at step 3 due to friction" |
| `HYPOTHESIS` | A specific, testable prediction | "Reducing to 3 steps lifts completion 15% in 2 weeks" |
| `OPTION` | A choice considered (may be more than one) | "Reduce steps" vs. "Add progress indicator" |
| `DECISION` | The chosen option, or later, the graded outcome | "Chose: reduce steps" / "Outcome: +6% (below prediction)" |

### Edge schema

```python
class GraphEdge(BaseModel):
    id: str
    from_node: str
    to_node: str
    edge_type: EdgeType   # supports | contradicts | depends_on | leads_to | derived_from
    created_at: datetime
```

### Edge types in use

- `derived_from` — an ASSUMPTION or HYPOTHESIS derived from a FACT
- `supports` / `contradicts` — the outcome DECISION connects back to the original HYPOTHESIS, marking whether the prediction held
- `leads_to` — an OPTION leads to the chosen DECISION
- `depends_on` — a HYPOTHESIS depends on an ASSUMPTION being true

### Storage — simplified for a one-day build

Two Postgres tables, not a full graph database:

```sql
CREATE TABLE graph_nodes (
    id UUID PRIMARY KEY,
    bet_id UUID NOT NULL,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    confidence FLOAT,
    source TEXT,
    status TEXT DEFAULT 'ACTIVE',
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE graph_edges (
    id UUID PRIMARY KEY,
    from_node UUID REFERENCES graph_nodes(id),
    to_node UUID REFERENCES graph_nodes(id),
    edge_type TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT now()
);
```

Postgres is the source of truth for everything. All external app writes
(GitHub, Calendar, Slack) are side effects that flow from Postgres state; if a
side effect fails, the graph record still exists and the failure is flagged.

This is queryable with plain SQL for the demo ("show me every hypothesis and
whether it panned out") without needing a graph database — the graph structure
lives in the edges table, not in the storage engine. If there's spare time near
the end, a NetworkX in-memory view built from these two tables makes a nice
visual for the demo (nodes colored by type, edges drawn between them) — but
that's a nice-to-have, not required for the core loop.

---

## 2. Pipeline Flow (LangGraph)

### Shared state

```python
class BetState(TypedDict):
    bet_id: str
    raw_message: str              # original meeting transcript text
    extracted_nodes: list[GraphNode]
    extracted_edges: list[GraphEdge]
    proposed_option_id: str | None   # LLM's guess at chosen option, pre-confirmation
    chosen_option_id: str | None     # set only after PM confirms in Slack
    github_issue_url: str | None
    review_date: str | None
    metric_name: str | None
    predicted_lift: float | None
    actual_lift: float | None
    delta: float | None
    calibration_score: float | None
    flags: list[str]              # fail-visible notes, e.g. "posthog_timeout"
```

### Phase 1 — Intake (runs once, per planning meeting)

1. **`docs_intake`** — fetches the meeting transcript via the Google Docs API (same service account/credentials already verified for Sheets), populates `raw_message` with the full transcript text
2. **`extract_facts`** — LLM call that parses the transcript into discrete `FACT` nodes only — one per distinct factual/observational statement someone made ("we saw a 12% drop-off at step 3", "the API rate limit hasn't changed"). Tags each with which speaker said it in `source`, e.g. `"meeting:2026-09-13:speaker=PM"`. No assumptions or hypotheses are created at this step — just what was actually said
3. **`derive_reasoning`** — a second LLM call that looks at the `FACT` nodes just written for this `bet_id` and derives `ASSUMPTION`, `HYPOTHESIS` (with `confidence`), and `OPTION` nodes from them. Every derived node gets a `derived_from` edge back to the fact(s) that motivated it — this is what makes the graph traceable rather than just a flat list: you can always answer "why do we believe this?" by walking the edge back to what was actually said in the room. This step also picks `proposed_option_id` — its best guess at which option the PM chose, based on the transcript
4. **`confirm_decision`** — Slack message to the PM: "I think you chose **Option B: reduce to 3 steps** — correct?" with [Confirm] / [No, it was Option A] buttons. LangGraph interrupt waits here. The PM's click resumes the graph; only now is the DECISION node written:`chosen_option_id` is set, a DECISION node is created, a `leads_to` edge is written from the chosen OPTION, and `depends_on` edges are written from the HYPOTHESIS to its supporting ASSUMPTION(s). This is the human checkpoint that makes the record trustworthy — everything else in the graph is machine-proposed; this one thing is human-attested. If the PM never responds, the bet stays in a `PENDING_CONFIRMATION` state visible in the flags — nothing silently becomes a DECISION
5. **`link_github`** — attach `bet_id` + summary as a comment on (or creation of) a GitHub issue; store `github_issue_url`
6. **`schedule_review`** — create a Calendar event at the hypothesis's stated measurement window; store `review_date`
7. `END` — bet is now logged, human-confirmed, linked, and scheduled

### Phase 2 — Review (triggered later)

Production trigger: the Calendar event fires (webhook). Demo trigger: `check_due_reviews()` runs the identical code path for all bets with `review_date <= now()`.

1. **`review_trigger`** — loads the `BetState` for that `bet_id`
2. **`fetch_metric`** — query PostHog (HogQL) for `metric_name` over the measurement window; populate `actual_lift`
3. **`compare_outcome`** — compute `delta = actual_lift - predicted_lift`; create a new `DECISION` node representing the outcome, with a `supports` edge to the original `HYPOTHESIS` if |delta| <= 5pp, `contradicts` otherwise; mark the HYPOTHESIS `RESOLVED`
4. **`update_calibration`** — recompute the PM's calibration across all resolved bets: **mean absolute delta** (headline number) + **hit rate within 5pp** (secondary). Both are one-line SQL aggregates over resolved HYPOTHESIS nodes joined to their outcome DECISIONs
5. **`report_outcome`** — Slack message to the PM with the result (see report format below); optionally a comment back on the original GitHub issue closing the loop visibly
6. `END`

### Slack report format (Phase 2 payoff screen — mock this first)

```
📊 Bet resolved: "Reduce signup to 3 steps"
  Predicted: +15% completion in 2 weeks (LLM confidence: 0.7)
  Actual:    +6%  →  delta -9pp → ❌ contradicted beyond tolerance
  PM calibration: mean |delta| = 7.2pp across 4 resolved bets (2 within tolerance)
  What broke the assumption: <1-line LLM summary of what the graph says>
```

That last line (walk the graph, name the ASSUMPTION farthest upstream that the
outcome contradicts) is the demo's "wow" line — it's only possible because the
reasoning graph exists.

### Fail-visible handling

If PostHog fails to return a metric, or GitHub/Calendar/Slack writes fail, the
pipeline does not crash: append to `flags` (e.g. `"posthog_timeout"`,
`"github_write_failed"`) and let `report_outcome` say plainly "couldn't fetch
the metric, here's why, here's when we'll retry" rather than silently skipping
the report. The graph record is already safe in Postgres before any side effect
is attempted — a failed delivery never loses a decision.

---

## 3. Demo data plan (critical — do this early)

Phase 2's demo depends on PostHog actually having data for the seeded bets.
Plan:

1. **Seed 2-3 backdated bets** directly into the two tables (skip Phase 1 for these) — a mix: one clearly confirmed hypothesis, one clearly contradicted, one near the tolerance boundary
2. **Seed PostHog with matching synthetic events** so each seeded bet's HogQL query returns a real number. Do not assume existing PostHog data lines up with your bet stories — scripted synthetic events (a small Python script against the PostHog capture API) are the reliable path. If PostHog seeding proves fragile, the fallback is a stub `fetch_metric` that returns the seeded values, flagged as stubbed — but real PostHog queries make the demo much stronger
3. One of the seeded bets should have `review_date` in the past so the demo's live Phase 2 run resolves it on screen

---

## 4. What to build in what order tomorrow

1. Node/edge Pydantic models + the two Postgres tables — nothing else works without this
2. `extract_facts` and `derive_reasoning` — the actual reasoning steps, get these working against a hardcoded sample transcript first, before wiring the Google Docs API
3. **Graph sanity check** — right after extraction works, print or dump the resulting nodes/edges (even a text adjacency listing) and eyeball it: can you actually walk "why do we believe this hypothesis?" back to a transcript fact? Fix extraction prompts now, not on demo day. This is also the artifact you show judges when they ask "show me the agent's reasoning"
4. Google Docs intake + `confirm_decision` (Slack buttons + interrupt) + `link_github` + `schedule_review` — Phase 1 end-to-end
5. Seed backdated bets + seed PostHog events (Section 3)
6. `fetch_metric` (PostHog) + `compare_outcome` + `update_calibration` + `report_outcome` — Phase 2 end-to-end, triggered via `check_due_reviews()`
7. Demo script, reliability brief, submission docs

### Demo narrative (2 min, sketch)

1. Show yesterday's meeting doc → run Phase 1 → Slack confirm-click on screen → show GitHub issue + Calendar event created
2. Show the reasoning graph printout: "here's why it believed that, traced to what was actually said"
3. Run `check_due_reviews()` → live PostHog query → Slack report lands with delta + calibration score
4. Kill PostHog mid-demo (or pre-record it) → flags show, report still goes out saying what failed → reliability story told in 15 seconds, live
