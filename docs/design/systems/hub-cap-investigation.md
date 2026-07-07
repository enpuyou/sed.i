# Hub-Entity Fan-out Investigation

**Date**: 2026-07-06
**Scope**: 5 queries where S2/S3 (entity lane) regress vs S1 (chunks only)
**Method**: Traced `_entity_search` internals query-by-query; no estimated values

---

## Summary

The entity lane adds net value across the full 45-query dataset but regresses on 5 specific queries. There are **two distinct failure modes** and hub-cap adjustment does not fix either of them.

Additional finding: the `entity_relations` graph has only 80 edges total — graph expansion contributes negligibly to results. The regressions are entirely from **direct entity→article scoring**, not from 1-hop expansion.

| Mode | Queries affected | Root cause |
|------|-----------------|------------|
| **Entity vocabulary mismatch** | `ai_agent_autonomy`, `ai_tools_creative_workers`, `fde_ai_economy` | Expected articles contain only low-sim entities for the query; wrong entities score higher and win the direct-mention competition |
| **Claude-family name fragmentation** | `anthropic_products_direct`, `anthropic_claude_products` | 5 Claude-variant entities (Claude, Claude.ai, Claude Code, Claude Agent SDK, Claude Opus 4.6) each point to different subsets of articles; the expected targets lack a high-sim entity path |

**Hub cap is not the lever**: lowering `_ENTITY_HUB_ARTICLE_CAP` from 4→3 was tested — zero change in entity-lane R@10 for all 5 regression queries. The expansion path via entity_relations (80 total edges, sparse) has negligible effect on results. The problem is scoring of direct entity→article links, not graph traversal.

---

## Per-query findings

### `ai_agent_autonomy` — A=0.75, B=0.50, C=0.25, D=0.25 (worst regression)

**Query**: "autonomous AI agents making decisions without human oversight"

Gate passes at top_sim=0.506 on `Jagged Frontier of AI ability`. But this entity exists in exactly one article: `management_ai_superpower` — not one of the 4 expected articles. The full entity top-8 all score between 0.40–0.51 on AI-adjacent concepts but none links to the expected articles (`why_context_engineering`, `trustworthy_agents`, `lecture_06_initialize`, `building_agents_sdk`).

Entity lane top-5:
1. `anthropic_economic_index` — via Directive Conversations + Automation
2. `management_ai_superpower` — via Jagged Frontier of AI ability
3. `fde_hottest_role` — via Agentforce + Artificial Intelligence (AI)
4. `automated_alignment` — via Scalable oversight
5. `anthropic_institute_focus` — via AI-driven R&D

Zero expected articles in the top-10. The entity lane is 100% noise for this query.

**Why S1 > S2**: S1 retrieves `trustworthy_agents` at rank 1 (directly by chunk similarity). The entity lane at S2 pushes it out of the top-10 by polluting with off-topic articles that accumulate small entity-sim contributions.

**Root cause**: The query's concept-space (agent autonomy, oversight) has no precise entities in the graph — the closest entities are metaphorical ("Jagged Frontier") or generic ("AI labs"). The gate threshold of 0.40 is too permissive for concept-only queries.

---

### `anthropic_products_direct` — A=B=0.67, C=D=0.33

**Query**: "Anthropic Claude AI model capabilities"

Gate passes strongly (top_sim=0.631 on `Claude`). `Claude` appears in 4 articles — right at the hub cap boundary.

Entity lane top-5:
1. `anthropic_economic_index` — via Claude.ai + Anthropic AI Usage Index
2. `automated_alignment` — via Claude Opus 4.6 + Scalable oversight + Automated Alignment Researchers
3. `building_agents_sdk` — via Claude Agent SDK + Claude Code (hub-direct)
4. `ai_economics_81k` — via productivity gains + Claude
5. `what_is_claude` ✓ — via Claude Shannon + Claude

Expected articles `anthropic_sdk_python` and `anthropic_institute_focus` score low because:
- `anthropic_sdk_python` is only reached via `Anthropic Bedrock API` (dist=0.614, too low to rank high)
- `anthropic_institute_focus` is only reached via `The Anthropic Economic Index` (dist=0.641, below threshold cutoff in practice)

The name "Claude" being split across many entity variants (Claude, Claude.ai, Claude Code, Claude Agent SDK, Claude Opus 4.6) fragments the signal — each variant points to a different subset of articles, diluting precision.

---

### `anthropic_claude_products` — A=B=0.50, C=D=0.25

**Query**: "Anthropic Claude model product line"

Same Claude-family fragmentation. Top entity match is `Claude Code` (sim=0.533, 5 articles → hub-direct). This floods:
- `building_agents_sdk`, `management_ai_superpower`, `trustworthy_agents`, `skillopt`, `effective_context_engineering`

Expected `anthropic_sdk_python` has no entity path that reaches it with competitive score. `learn_claude_code` similarly unreachable — it's not in the graph as a distinct article-entity link.

---

### `fde_ai_economy` — A=B=1.0, C=D=0.8 (mild regression)

**Query**: "forward deployed engineers and the emerging AI economy"

Entity lane correctly retrieves 4/5 expected articles in top-7. The one miss is `management_ai_superpower` — it has no FDE-related entity. The intruder pushing it out is `notes_ai_labor_china` (rank 4), reaching via "Works Progress Administration for the AI era" (adjacent concept).

This is the softest failure: entity lane retrieves 4 of 5 expected, but `management_ai_superpower` has no entity connection to FDE concepts at all — it's a semantic-only match. The entity lane can't manufacture a path that doesn't exist.

---

### `ai_tools_creative_workers` — A=0.40, B=0.60, C=D=0.40

**Query**: "impact of AI tools on creative professionals"

S1 retrieves `ai_economics_81k`, `notes_ai_labor_china`, `anthropic_economic_index`, `anthropic_institute_focus`, `ai_engineer_job_outlook` — 3 of 5 expected in top-5 (R@10=0.60).

Entity lane promotes `anthropic_economic_index` to rank 1 (via Claude.ai + Anthropic AI Usage Index — not related to creative workers at all). Three expected articles (`banality_recommendation`, `year_in_slop`, `llms_slot_machines`) have no entity path to the query — they're cultural critique articles with no AI-tool entities linked.

The entity lane adds noise without adding any missing signal here.

---

## Failure pattern taxonomy

```
                    Entity gate passes?
                    /           \
                YES              NO → no entity lane, falls back to S1 behavior
               /    \
     Correct entity   Wrong entity
     → correct        → off-topic article wins (ai_agent_autonomy, ai_tools)
       articles
          |
     Expected article
     has entity path?
          |         \
         YES         NO → expected article unreachable via entities (fde_ai_economy)
          |
     Entity is hub (many articles)?
          |            \
         YES            NO → correct, works as intended
          |
     Hub cap enforced?
          |              \
         YES (>4)         NO (=4, Claude) → fragments signal, wrong articles win
          |                                   (anthropic_products_direct)
     Direct-only (ok-ish)
```

---

## Why threshold adjustment won't fix ai_agent_autonomy

The query "autonomous AI agents making decisions without human oversight" causes the gate to pass at top_sim=0.506 on `Jagged Frontier of AI ability`. Raising the threshold to 0.50 does NOT prevent this — 0.506 > 0.50. And even if it did, the problem is deeper:

The entities that *should* bridge to the 4 expected articles have far lower sim scores than the entities causing fan-out:

| Entity | Sim to query | Article bridged | Status |
|--------|-------------|-----------------|--------|
| `Jagged Frontier of AI ability` | 0.506 | `management_ai_superpower` | Intruder — wins |
| `Scalable oversight` | 0.506 | `automated_alignment` | Intruder — wins |
| `subagents` | 0.401 | `building_agents_sdk` ✓ | Correct — barely passes |
| `Anthropic` (org) | 0.388 | `trustworthy_agents`, `lecture_06_initialize` ✓ | Correct — below 0.40, filtered out |
| `Deep Research Agent` | 0.359 | `why_context_engineering` ✓ | Correct — below threshold |
| `Claude Agent SDK` | 0.348 | `building_agents_sdk` ✓ | Correct — below threshold |

**The semantic gap**: the query uses abstract autonomy/oversight language; the expected articles are about *implementation* (SDK usage, context engineering, lecture notes). Their entities are technical, not conceptual — they score low against the abstract query. No threshold adjustment can fix a structural gap between query vocabulary and article-entity vocabulary.

This is a **missing-entity problem**, not a threshold problem. The expected articles need entities that speak to the autonomy/oversight concept: e.g., `human-in-the-loop`, `autonomous decision-making`, `agent oversight` — concepts that aren't currently extracted because the articles focus on how to build agents, not on the autonomy debate.

---

## True failure taxonomy (revised)

| Query | Root cause | Fixable by hub cap? | Fixable by threshold? | Real fix |
|-------|-----------|--------------------|-----------------------|----------|
| `ai_agent_autonomy` | Wrong entities score higher than correct ones; correct entity sims below 0.40 | No | No | Extract conceptual entities from expected articles |
| `anthropic_products_direct` | Claude-family fragmentation; `Claude Code` (hub-direct, 5 arts) floods results | Partially | No | Lower hub cap to 3 OR deduplicate Claude-family entities |
| `anthropic_claude_products` | Same Claude-family fragmentation | Partially | No | Same |
| `ai_tools_creative_workers` | `Claude.ai` (sim=0.548) hijacks top slot; expected articles have no entities | No | No | Extract `creative labor`, `platform culture` entities from `banality_recommendation` etc |
| `fde_ai_economy` | `management_ai_superpower` has no FDE entity — unreachable via entity graph | No | No | Entity extraction gap (the article is a narrative, not technical) |

---

## Actionable mitigations

### Mitigation 1: Extract missing conceptual entities (highest leverage)

The core problem for `ai_agent_autonomy` and `ai_tools_creative_workers` is that expected articles contain only implementation-level entities (SDK names, tool names) that score low against concept-level queries.

Articles needing re-extraction with conceptual emphasis:
- `trustworthy_agents`, `lecture_06_initialize`, `why_context_engineering`, `building_agents_sdk` — need concepts like `human-in-the-loop`, `autonomous decision-making`, `agent oversight`, `agentic AI`
- `banality_recommendation`, `year_in_slop`, `llms_slot_machines` — need `AI-generated content`, `creative labor displacement`, `platform recommendation culture`

Run `analyze_article_task` with a prompt variant that explicitly asks for *conceptual themes and debates* alongside named entities. Re-extract for these 7 articles only (no full re-run needed).

### Mitigation 2: Claude-family entity deduplication

Entities `Claude`, `Claude.ai`, `Claude Code`, `Claude Agent SDK`, `Claude Opus 4.6` fragment signal across disjoint article subsets. For `anthropic_products_direct`, `Claude` (sim=0.631) sends weight to `ai_economics_81k` and `harness_design` instead of `anthropic_sdk_python`.

Two implementation options:
- **Merge at extraction time**: emit `Claude (AI assistant)` as canonical for all Claude-product mentions, link to all articles that mention any Claude variant. One post-processing pass.
- **Merge at query time**: when entity name matches `^Claude`, boost weight toward core Claude articles. More complex, harder to maintain.

### Mitigation 3: Hub cap adjustment — confirmed ineffective, do not pursue

Tested empirically: lowering `_ENTITY_HUB_ARTICLE_CAP` from 4→3 produces zero R@10 change on all 5 regression queries. The `entity_relations` graph has only 80 edges — expansion is negligible. Regressions come from direct entity→article scoring, not graph traversal. Hub cap only controls expansion eligibility.

---

## Recommended experiment sequence

1. **M1 first**: re-extract 7 articles with conceptual-entity prompt, rerun eval on `ai_agent_autonomy` and `ai_tools_creative_workers` only (fast check, no full eval needed)
2. **M2 second**: Claude-family dedup pass, rerun `anthropic_products_direct` and `anthropic_claude_products`
3. After both: full 45-query eval to confirm no regressions in tier 1–3 and tier-4 wins hold
4. **Do not touch hub cap** — it is inert for this problem
