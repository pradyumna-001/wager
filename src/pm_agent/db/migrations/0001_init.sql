-- 0001_init.sql — core schema (docs/02_DATA_MODEL.md)
CREATE TABLE IF NOT EXISTS graph_nodes (
    id          UUID PRIMARY KEY,
    bet_id      UUID NOT NULL,
    type        TEXT NOT NULL CHECK (type IN ('FACT','ASSUMPTION','HYPOTHESIS','OPTION','DECISION')),
    content     TEXT NOT NULL,
    confidence  DOUBLE PRECISION,
    source      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'ACTIVE'
                CHECK (status IN ('ACTIVE','RESOLVED','SUPERSEDED')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_nodes_bet ON graph_nodes(bet_id);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON graph_nodes(type);

CREATE TABLE IF NOT EXISTS graph_edges (
    id         UUID PRIMARY KEY,
    bet_id     UUID NOT NULL,
    from_node  UUID NOT NULL REFERENCES graph_nodes(id),
    to_node    UUID NOT NULL REFERENCES graph_nodes(id),
    edge_type  TEXT NOT NULL CHECK (edge_type IN
               ('derived_from','supports','contradicts','leads_to','depends_on')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_edges_bet ON graph_edges(bet_id);
CREATE INDEX IF NOT EXISTS idx_edges_from ON graph_edges(from_node);
CREATE INDEX IF NOT EXISTS idx_edges_to ON graph_edges(to_node);

-- Bets themselves (created at Phase 1 start)
CREATE TABLE IF NOT EXISTS bets (
    id           UUID PRIMARY KEY,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    meeting_date DATE NOT NULL,
    doc_id       TEXT NOT NULL,
    pm_slack_id  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'EXTRACTING'
                 CHECK (status IN
                 ('EXTRACTING','PENDING_CONFIRMATION','CONFIRMED',
                  'SCHEDULED','RESOLVED','FLAGGED')),
    metric_name     TEXT,
    metric_query    TEXT,
    predicted_lift  DOUBLE PRECISION,
    review_date     DATE,
    github_issue_url TEXT,
    calendar_event_id TEXT,
    flags        JSONB NOT NULL DEFAULT '[]'   -- serialized DataFlag list, appended on failures
);

-- Outcomes: one row per resolved bet; keeps calibration a single aggregate query.
CREATE TABLE IF NOT EXISTS outcomes (
    bet_id      UUID PRIMARY KEY REFERENCES bets(id),
    hypothesis_node_id UUID NOT NULL REFERENCES graph_nodes(id),
    decision_node_id   UUID NOT NULL REFERENCES graph_nodes(id),
    actual_lift   DOUBLE PRECISION NOT NULL,
    predicted_lift DOUBLE PRECISION NOT NULL,
    delta          DOUBLE PRECISION NOT NULL,
    within_tolerance BOOLEAN NOT NULL,
    resolved_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
