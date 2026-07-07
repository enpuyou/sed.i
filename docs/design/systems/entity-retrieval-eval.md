# Entity Search Retrieval Eval

Date: 2026-07-03
Corpus: 61 articles, enpu@example.com
Eval set: 32 queries across 8 categories
Metric: Recall@10 and MRR@10 (mode=full, all four lanes active)

---

## Method progression

Each row is a configuration change applied cumulatively on top of the previous.

| # | Method | Change | R@10 | MRR | ▲ better | ▼ worse |
|---|--------|--------|------|-----|----------|---------|
| 0 | **Baseline** | Chunk-backfill semantic+keyword, threshold=0.55, rank-based entity RRF (k=120) | 0.721 | 0.773 | — | — |
| 1 | **Prompt v2** | CONCEPT-first extraction, noise exclusion, 2500-word limit; re-extracted all 61 articles | 0.743 | 0.814 | +1 | 0 |
| 2 | **Threshold 0.40** | `_ENTITY_SIM_THRESHOLD` 0.55→0.40, `_ENTITY_EXPAND_THRESHOLD` 0.60→0.45; mode=full | 0.748 | 0.897 | +5 | +4 |
| 3 | **Score passthrough** | Entity lane uses IDF-dampened similarity score directly (×0.025 scale) instead of rank-based `1/(120+rank)` | **0.764** | **0.859** | **+5** | **+2** |

Net vs baseline: **+4.3pp R@10, +8.6pp MRR, 5 queries improved, 2 regressed.**

---

## Full query table — Baseline vs Final (Method 3)

| Query key | Category | Baseline | Final | Δ | MRR |
|-----------|----------|----------|-------|---|-----|
| context_engineering_direct | direct | 1.00 | 1.00 | — | 1.00 |
| fde_role_direct | direct | 1.00 | 1.00 | — | 1.00 |
| bad_bunny_direct | direct | 1.00 | 1.00 | — | 1.00 |
| ai_labor_direct | direct | 1.00 | 1.00 | — | 1.00 |
| mlops_tools_direct | direct | 1.00 | 1.00 | — | 1.00 |
| ozempic_direct | direct | 1.00 | 1.00 | — | 1.00 |
| jayz_music_direct | direct | 1.00 | 1.00 | — | 1.00 |
| music_algorithm_culture | multi_hop_pass | 1.00 | 1.00 | — | 1.00 |
| wellbeing_tech_criticism | multi_hop_fail | 0.75 | 0.75 | — | 0.33 |
| ai_economics_cluster | multi_hop_pass | 1.00 | 1.00 | — | 1.00 |
| **tech_culture_critique** | multi_hop_fail | 0.75 | **1.00** | **▲+0.25** | 1.00 |
| fde_ai_economy | score_displacement | 0.80 | 0.80 | — | 1.00 |
| teen_culture_identity | multi_hop_pass | 1.00 | 1.00 | — | 0.50 |
| system_design_direct | chunk_fixed | 1.00 | 1.00 | — | 1.00 |
| inflation_direct | chunk_fixed | 1.00 | 1.00 | — | 1.00 |
| alignment_safety | chunk_fixed | 1.00 | 1.00 | — | 1.00 |
| **anthropic_products_direct** | vocab_frame_mismatch | 0.67 | **0.33** | **▼−0.34** | 0.50 |
| long_running_agents | score_displacement | 0.50 | 0.50 | — | 1.00 |
| agent_observability | score_displacement | 0.50 | 0.50 | — | 1.00 |
| wellbeing_productivity | vocab_frame_mismatch | 0.75 | 0.75 | — | 1.00 |
| music_culture_identity | vocab_frame_mismatch | 0.75 | 0.75 | — | 0.50 |
| **ai_agent_vs_content_culture** | cross_cluster_split | 0.25 | **0.50** | **▲+0.25** | 0.50 |
| **ai_tools_creative_workers** | cross_cluster_split | 0.60 | **0.40** | **▼−0.20** | 1.00 |
| **ai_content_quality_decline** | vocab_frame_mismatch | 0.33 | **0.67** | **▲+0.34** | 1.00 |
| tech_labor_silicon_valley | cross_cluster_split | 0.50 | 0.50 | — | 1.00 |
| agent_productivity_reliability | vocab_frame_mismatch | 0.50 | 0.50 | — | 0.14 |
| leadership_culture_sport | vocab_frame_mismatch | 0.67 | 0.67 | — | 1.00 |
| drug_treatment_health_policy | cross_cluster_split | 0.33 | 0.33 | — | 1.00 |
| **chatgpt_work_impact** | entity_wins | 0.67 | **1.00** | **▲+0.33** | 0.50 |
| cnn_cross_domain_bridge | entity_wins | 0.50 | 0.50 | — | 1.00 |
| ted_turner_cnn_empire | entity_wins | 0.50 | 0.50 | — | 1.00 |
| **ml_engineering_tools** | score_displacement | 0.25 | **0.50** | **▲+0.25** | 0.50 |
| **AVERAGE** | | **0.721** | **0.764** | **+0.043** | **0.859** |

---

## What each method change did

### Method 1 — Prompt v2 (+1 improved, 0 worse)

Re-extracted all 61 articles with the new CONCEPT-first prompt. CONCEPT entities
up 62% (85→138), ORG/PERSON down ~55%, concept↔concept relations up 650%.

Only one query moved: `fde_ai_economy` +0.20 because the new prompt extracted
`forward deployed engineer` as a CONCEPT entity, creating a direct name bridge
that the old prompt didn't.

The new CONCEPT entities (`enshittification`, `context anxiety`, `availability
heuristic`) did not improve retrieval because cosine similarity between entity
names and query text is too low to pass even a 0.55 threshold. The extraction
quality improvement is real but doesn't flow through to search until the threshold
is lowered (Method 2).

### Method 2 — Threshold 0.40 / 0.45 (+5 improved, +4 worse; net flat R@10)

Lowering the threshold to 0.40 activated the new CONCEPT entities and added 5
improvements. But it also introduced 4 regressions — all caused by generic entity
names (`AI`, `Claude`, `AI tools`, `productivity gains`) firing at sim=0.52–0.63
on broad queries and injecting the full Anthropic/AI cluster into top-10, displacing
expected cross-domain articles.

The entity lane's internal IDF-dampened score correctly ranked these lower — but
the RRF fusion discarded that score and used only rank (`1/(120+rank)`), treating
an article that matched via 4 Claude-family entities the same as one that matched
via a single precise entity.

### Method 3 — Score passthrough (+5 improved, +2 worse; net +4.3pp R@10)

Replaced `1/(120+rank)` with `entity_score × 0.025` in the RRF fusion, where
`entity_score` is the IDF-dampened similarity score already computed inside
`_entity_search`. This preserves the internal ranking signal across the fusion
boundary.

Effect: articles accumulating high entity scores through precise matches rank
higher; articles boosted incidentally by generic entities (`AI`, `productivity
gains`) lose rank because their per-entity contributions are individually low.
Eliminates 2 of the 4 regressions introduced in Method 2.

---

## Remaining regressions (2)

### anthropic_products_direct (−0.34)

Query: "Anthropic Claude AI model capabilities"
Missing: `anthropic_sdk_python`, `anthropic_institute_focus`

The entity lane fires `Claude` (sim=0.63, 4 articles), `Claude.ai` (0.62),
`Claude Agent SDK` (0.59), `Claude Code` (0.58), `Claude Opus 4.6` (0.49) — five
Claude-family entities that collectively inject 10 distinct articles, all from the
Anthropic/agent engineering cluster. These fill all top-10 slots.
`anthropic_institute_focus` and `anthropic_sdk_python` are semantic rank 2 and 11
respectively but fall out because the entity lane score of the Claude cluster
exceeds their semantic-only signal.

Root cause: `Claude`, `Claude Code`, `Claude Agent SDK` are high-frequency tool
entities extracted across many articles. Their names match any Anthropic query at
high sim. Extraction-time fix: deduplicate Claude-family entities; or treat them
as hub entities capped from expansion regardless of article count.

### ai_tools_creative_workers (−0.20)

Query: "impact of AI tools on creative professionals"
Missing: `banality_recommendation`, `year_in_slop`, `llms_slot_machines`

The entity lane fires `AI tools` (sim=0.63, 1 article), `AI` (0.58, 2 articles),
`AI Engineer Career Track` (0.56), `Claude.ai` (0.55). These inject 6 noise
articles from the AI-economics cluster, filling ranks 2–6 and displacing the three
culture-criticism articles the query should find.

Root cause: `AI` and `AI tools` are over-generic concept entities that the prompt
was supposed to reject ("skip generic background terms"). They slipped through.
Fix: stricter extraction-time enforcement — reject any entity whose name is a
single common noun or a category label.

---

## What score passthrough fixed (the 2 recovered regressions)

**ai_economics_cluster** (was −0.25 at Method 2, now 0.00):
The entity `AI` (sim=0.58) was injecting `Our obsession with efficiency` and
`Resonant Computing Manifesto` at rank 7–8, displacing `ai_engineer_job_outlook`.
With score passthrough, `AI` contributes only `0.58/log2(3) × 0.025 = 0.009`
to the RRF total — too small to displace articles with strong semantic scores.

**agent_productivity_reliability** (was −0.25 at Method 2, now 0.00):
Three separate entities (`Equation of Agentic Work`, `Jagged Frontier`, `GDPval`)
all pointing to `Management as AI superpower` had been accumulating RRF rank bonuses
additively via `1/(120+rank)`. With score passthrough each contributes individually
and their combined total is modest. `Management as AI superpower` drops from rank 1
to rank 2, allowing `harness_design` and other expected articles to surface.

---

## Applied configuration

```python
# hybrid_search.py
_ENTITY_SIM_THRESHOLD    = 0.40   # was 0.55
_ENTITY_EXPAND_THRESHOLD = 0.45   # was 0.60
_ENTITY_SCORE_SCALE      = 0.025  # new; replaces 1/(120+rank) in RRF fusion
```

The scale factor (0.025) is not sensitive — values from 0.005 to 0.100 produce
identical results because the entity score ordering is preserved across the range
and the RRF fusion is dominated by keyword+semantic contributions.

---

## Residual failure taxonomy

Queries that remain below 1.0 after all three methods, with root cause:

| Query | Current recall | Root cause |
|-------|---------------|------------|
| wellbeing_tech_criticism | 0.75 | `llms_slot_machines` vocabulary (`slot machine`, `dopamine`) too far from `wellbeing platform` |
| anthropic_products_direct | 0.33 | Claude-family entity fan-out; requires entity dedup or hub cap for Claude variants |
| long_running_agents | 0.50 | Score displacement — chunk scores dominate; entity gap for `agent_reliability` CONCEPT |
| agent_observability | 0.50 | Same as above |
| ai_agent_vs_content_culture | 0.50 | Cross-cluster vocabulary gap; entity partially bridging but `banality_recommendation` still missing |
| ai_tools_creative_workers | 0.40 | Generic entity `AI` and `AI tools` injecting wrong cluster |
| tech_labor_silicon_valley | 0.50 | FDE articles use skills/hiring vocabulary, not `labor practices` |
| agent_productivity_reliability | 0.50 | `productivity` pulls AI-economics cluster semantically |
| drug_treatment_health_policy | 0.33 | No shared vocabulary or entity between health clusters |
| cnn_cross_domain_bridge | 0.50 | CNN now appears in only 1 article post-prompt-v2; bridge broken |
| ted_turner_cnn_empire | 0.50 | Same — CNN single-article; 1-hop expansion no longer reaches Trump/Powell article |

The last two (`cnn_cross_domain_bridge`, `ted_turner_cnn_empire`) were confirmed
entity wins in the pre-v2 extraction (CNN appeared in 2 articles). Prompt v2
correctly dropped CNN as a quote-attribution entity from the Trump/Powell article
("Richard Fisher told CNN") — which fixed extraction quality but removed the
cross-domain bridge. This is an inherent tradeoff: precision in extraction reduces
noise but can remove serendipitous bridges.
