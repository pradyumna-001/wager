# Context

## Hackathon brief

Build an AI agent that takes action across multiple external apps. Show how you know it works.

Evaluation:
- 30% Judging
- 25% Technical execution
- 20% Reliability & evaluation
- 15% Usefulness
- 10% Originality
- 10% Demo clarity

Requirement: at least three external apps, working project, two-minute demo, system/reliability brief.

## FinAgent baseline

FinAgent is a multi-agent LangGraph pipeline for Brazilian asset managers.
- 5 agents: MacroAgent, CompanyAgent, QuantAgent, RiskAgent, EditorAgent
- Generates daily morning notes + buy/sell/keep recommendations for B3 equities at 6:00 AM BRT
- Tech: Python 3.11, FastAPI, LangGraph, NVIDIA NIM, Tavily, yfinance, PostgreSQL 18 + Apache AGE, Redis + Celery, pgvector, LangSmith

**Current gap:** pipeline ends at writing a note to Postgres. No delivery, no decision capture, no accountability.

## Why this fits

The hackathon asks for multi-app *action*. FinAgent is a strong multi-step analyst. Adding delivery + decision capture turns it into a multi-app action agent without abandoning the domain.

DDIA references that guide the extension:
- **Designing Data-Intensive Applications, Ch. 1 – Reliability, Scale, Maintainability**: FinAgent already enforces reliability invariants (freshness, RLS, fail-visible). The extension must preserve them.
- **Ch. 5 – Replication**: Google Sheets is a dual-write target. We treat Sheets as a read-optimized replica for managers, not the source of truth. Postgres remains source of truth; Sheets sync is best-effort with idempotent writes.
- **Ch. 7 – Transactions**: The LangGraph interrupt + approval is a long-running transaction. We keep state in the AgentState with reducers, and treat the approval as an inbound event that resumes the same transaction context.
- **Ch. 9 – Consistency**: Decision records need *atomicity* between Postgres and the channel acknowledgment. We record approvals in Postgres first, then emit side-effects (Sheets, Calendar). If Sheets fails, we retry; the decision is not lost.
- **Ch. 11 – Stream processing**: Telegram updates are an inbound event stream. We handle them with a lightweight poller/sidecar that injects events into the graph checkpoint.
