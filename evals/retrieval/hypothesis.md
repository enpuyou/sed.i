---
eval: retrieval
status: promoted
measured: 2026-07-06
---

# Retrieval Eval — Hypothesis

## Evaluating
Four cumulative retrieval strategies for the hybrid search system:
- Variant A: item-level embeddings only (S0)
- Variant B: + chunk embeddings RRF-fused with keyword (S1)
- Variant C: + entity lane, rank-based RRF, no score passthrough (S2)
- Variant D: + entity lane with IDF-dampened score passthrough × 0.025 (S3 — current production)

## Hypothesis
"Each successive variant will improve Recall@10 over the previous by closing a specific retrieval gap: chunks fix vocabulary dilution in long articles; entity lane bridges articles connected by a named entity or concept node but sharing no surface vocabulary."

Falsified by: any variant that does not improve over its predecessor on the query type it was designed to fix.

## Success cases (where each upgrade should help)
- S0→S1: `alignment_safety` — what_is_claude buried under intro text, chunks expose RLHF sections
- S1→S2: `chatgpt_work_impact` — efficiency_humanity and year_in_slop share no vocabulary, only ChatGPT entity bridges them
- S2→S3: `tech_culture_critique` — llms_slot_machines uses slot-machine framing; concept passthrough score re-weights the entity contribution

## Guard cases (must not regress)
- All Tier 1 exact-match queries must stay at 1.00 across all variants
- `music_algorithm_culture`, `teen_culture_identity`, `fde_role_direct` must stay at 1.00

## Result (Phase 9 decision)
**Variant B (S1) ships as best aggregate R@10 (0.7652).** S2 and S3 show targeted wins on entity-bridge and concept-bridge queries but introduce hub-entity fan-out regressions that offset gains on aggregate. S3 is current production; hub cap is the next investigable fix before re-evaluating S2/S3 aggregate performance.

Confirmed wins by design:
- S0→S1: chunks fix `alignment_safety`, `attention_distraction_tech`
- S1→S2/S3: entity bridges `chatgpt_work_impact`, `tech_culture_critique`, `ai_content_quality_decline`

Confirmed regressions requiring investigation:
- Hub entities (Claude-family, generic AI): `anthropic_claude_products`, `anthropic_products_direct`, `fde_ai_economy`
- Entity fan-out on paraphrase queries: `ai_agent_autonomy`, `human_ai_collaboration_models`
