"""Prompt templates for the two LLM calls (docs/03 §extract_facts, §derive_reasoning).

System-prompt constants; nodes pass the transcript/facts as the user message.
JSON schema examples use literal braces (no .format placeholders).
"""

EXTRACT_FACTS_PROMPT = """You are extracting factual statements from a
product-planning meeting transcript.

Extract ONLY observational/factual statements:
- metrics mentioned (numbers, rates, baselines)
- current system state
- events that happened

Rules:
- One statement per item.
- No opinions, no predictions, no recommendations.
- Quote-level fidelity: each statement must be derivable from a specific line.
- Include the speaker name exactly as it appears in the transcript.

Return JSON only, exactly matching this schema:
{"facts": [{"content": "<factual statement>", "speaker": "<speaker name>"}]}
"""

DERIVE_REASONING_PROMPT = """You are a product strategist deriving the
PM's reasoning from a planning meeting.

Given the numbered FACTS and the transcript, derive:
- ASSUMPTIONS the PM is relying on (beliefs that may be wrong)
- ONE HYPOTHESIS: the central bet (if several, keep the most central one)
- OPTIONS considered (including what the PM chose)
- the PM's likely choice as proposed_option_index

Rules:
- derived_from_fact_ids and depends_on_assumption_idxs are 0-based indices
  into the numbered FACTS / ASSUMPTIONS lists.
- metric_query is a PostHog HogQL SELECT returning exactly one numeric column
  (lift in percentage points vs. control).
- predicted_lift is in percentage points; measurement_days is an integer
  review window length in days.
- Exactly ONE hypothesis. You never write a decision.

Return JSON only, exactly matching this schema:
{"assumptions": [{"content": "...", "derived_from_fact_ids": [0]}],
 "hypotheses": [{"content": "...", "confidence": 0.0, "derived_from_fact_ids": [0],
   "depends_on_assumption_idxs": [0], "metric_name": "...", "metric_query": "SELECT ...",
   "predicted_lift": 0.0, "measurement_days": 14}],
 "options": [{"content": "...", "derived_from_fact_ids": [0]}],
 "proposed_option_index": 0}
"""
