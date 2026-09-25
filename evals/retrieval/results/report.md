# Retrieval Eval — Full Experiment Report

**Date**: 2026-07-06
**Corpus**: 61 articles, user `enpu@example.com`
**Eval set**: 45 queries, 6 tiers
**Raw data**: `retrieval_full_20260706_215231.json`
**Runner**: `evals/retrieval/runner.py`

---

## Table of contents

1. [Metric definitions](#1-metric-definitions)
2. [Variants evaluated](#2-variants-evaluated)
3. [Extraction prompt history](#3-extraction-prompt-history)
4. [Query taxonomy](#4-query-taxonomy)
5. [Aggregate results](#5-aggregate-results)
6. [Per-tier breakdown](#6-per-tier-breakdown)
7. [Per-category breakdown](#7-per-category-breakdown)
8. [Full per-query table](#8-full-per-query-table)
9. [Entity bridge wins](#9-entity-bridge-wins)
10. [Entity regressions](#10-entity-regressions)
11. [Hub cap investigation](#11-hub-cap-investigation)
12. [Residual failures](#12-residual-failures)
13. [Phase 9 decision](#13-phase-9-decision)
14. [Next experiments](#14-next-experiments)

---

## 1. Metric definitions

**Recall@10 (R@10)** — the fraction of expected articles that appear anywhere in the top-10 results. Scale: 0.0–1.0. 1.0 means every expected article was retrieved; 0.0 means none were. A query with 4 expected articles where 3 appear in top-10 scores 0.75. This is the primary metric because the product shows a reading list, not a ranked single answer.

**MRR (Mean Reciprocal Rank)** — 1/rank of the first expected article in the result list. Scale: 0.0–1.0. MRR=1.0 means the first result is always an expected article; MRR=0.5 means the first expected article appears at rank 2 on average. Captures whether the most relevant article appears at the top, not just somewhere in 10.

**NDCG@10 (Normalized Discounted Cumulative Gain)** — a position-weighted recall: articles at rank 1 count more than articles at rank 9. Scale: 0.0–1.0. NDCG=1.0 means the expected articles appear at the very top of the list in ideal order. Lower than R@10 if expected articles are found but buried deep in the 10 results.

Aggregate values in this report are arithmetic means across all 45 queries. A 1pp (0.01) delta on 45 queries corresponds to roughly 0.45 queries changing — deltas below 2pp should be treated as directional, not definitive.

---

## 2. Variants evaluated

Four cumulative retrieval strategies. Each adds one component to the previous.

| Variant | Shorthand | What it does |
|---------|-----------|-------------|
| **A** | Item only | FTS keyword search RRF-fused with cosine similarity against item-level embeddings (one embedding per article). No chunk segmentation, no entity graph. |
| **B** | + chunks | Replaces item-level cosine with `_semantic_search`: uses the MAX similarity across all chunk embeddings for the 49 articles that have chunks; falls back to item embedding for the 12 that don't. Keyword lane unchanged. |
| **C** | + entity RRF | Adds an entity lane on top of B. Entity search results are treated as a third ranked list; only rank position is used in RRF (`1/(60+rank)`), raw similarity scores discarded. |
| **D** | + entity passthrough | Replaces rank-based entity RRF with IDF-dampened score passthrough: entity similarity score × 0.025 is added directly to the RRF sum. **Current production system** (`hybrid_search(mode="full")`). |

B is not strictly additive over A for the 12 articles without chunks (item embedding fallback is identical), but is strictly better for the 49 that have chunks.

C and D differ only in how the entity lane score is fused: C discards the magnitude; D preserves it scaled down.

---

## 3. Extraction prompt history

The entity graph is built by `_EXTRACTION_PROMPT` in `app/tasks/entity_extraction.py`. Two versions have been evaluated.

### Prompt v1 (superseded)

Focus: entity-type taxonomy only. No instruction to prefer CONCEPT over PERSON/ORG. Truncation: `text[:3000]` characters (≈600 words).

Typical output: heavy PERSON and ORGANIZATION extraction, minimal CONCEPT extraction. Example on "Our obsession with efficiency" — extracted `Harbour Bridge`, `Sydney train`, `HelloFresh`, `Chefgood` (scene-setting details), zero concept entities.

### Prompt v2 (current, in production)

Added: CONCEPT-first instruction, noise-exclusion rules ("skip generic background terms", "avoid quote sources"), relation types between concepts. Truncation: same `text[:3000]`.

**Extraction eval results** (10 articles, `entity-extraction-eval.md`):

| Metric | v1 | v2 | Delta |
|--------|----|----|-------|
| Total entities (corpus-wide) | 446 | 323 | −123 (−28%) |
| CONCEPT entities | 85 | 138 | **+53 (+62%)** |
| TOOL entities | 105 | 70 | −35 |
| ORGANIZATION entities | 120 | 56 | −64 (−53%) |
| PERSON entities | 108 | 45 | −63 (−58%) |
| Concept↔concept/tool relations | ~8 | ~60 | **+52 (+650%)** |

The reduction in total entities is intentional — fewer incidental mentions, more graph-useful signal per entity. The 650% increase in concept-concept relations is the primary quality gain: these are the edges that enable cross-domain traversal.

**Retrieval impact of v2** (32 queries, prior eval): aggregate R@10 unchanged at 0.74. One query improved (`fde_ai_economy` +0.20 via `forward deployed engineer` CONCEPT). The new CONCEPT entities don't improve retrieval because entity similarity to abstract queries remains below the 0.40 gate threshold. For example, `enshittification` has similarity 0.28 against the query "platform decay tech criticism" — below gate even with threshold at 0.40.

**Known remaining gap in v2**: narrative articles (`banality_recommendation`, `mindfulness`) produce 0 CONCEPT entities. Root cause: their conceptual argument develops in the second half of the article, past the 3000-character truncation. `banality_recommendation` discusses `algorithmic saturation`, `human curation as trust signal`, `affiliate marketing as editorial corruption` — all in sections not seen by the extractor.

---

## 4. Query taxonomy

### Tiers

Tiers are human-assigned labels (set in `retrieval_eval_dataset.py`) classifying query difficulty from simple lookup to cross-domain synthesis. Not inferred from experiment — assigned when the dataset was built.

| Tier | Description | What retrieval capability is needed |
|------|-------------|-------------------------------------|
| **T1** Exact match | Query contains the article's exact title words or named subject | Keyword or item embedding sufficient |
| **T2** Lexical cluster | Query uses related vocabulary from the same article but not exact title words | Chunk embeddings help (body text vs title mismatch) |
| **T3** Semantic paraphrase | Query rephrases the article's topic in different words | Semantic similarity across vocabulary |
| **T4** Entity bridge | Expected articles are vocabulary-disjoint; only a shared named entity connects them | Entity graph required |
| **T5** Concept bridge | Expected articles connected by a shared abstract concept, not a named entity | Concept entity extraction + entity graph |
| **T6** Cross-domain synthesis | Expected articles span multiple topic clusters; multiple bridge types needed | Full pipeline; hardest queries |

### Categories

Categories are human-assigned labels classifying *what kind of retrieval challenge* the query tests. Also set in `retrieval_eval_dataset.py`, not inferred from scores.

| Category | What it means |
|----------|--------------|
| `direct` | Query uniquely identifies one article; expected = 1 article |
| `chunk_fixed` | Answer buried in body text, not title/intro; chunk embeddings needed |
| `multi_hop_pass` | Multi-hop through entity/concept graph; known to work |
| `multi_hop_fail` | Multi-hop retrieval; known to require improvement |
| `entity_wins` | Entity lane provides genuine bridge between vocabulary-disjoint articles |
| `entity_regression` | Entity lane known to regress vs non-entity variants |
| `entity_partial` | Entity helps some expected articles, not all |
| `concept_bridge` | Abstract concept (not named entity) needed to bridge articles |
| `cross_cluster_split` | Expected articles span multiple unrelated topic clusters |
| `score_displacement` | High-scoring off-topic articles displace on-topic ones from top-10 |
| `vocab_frame_mismatch` | Query uses different framing than the article (different words, same topic) |

---

## 5. Aggregate results

| Variant | R@10 | MRR | NDCG@10 | Δ R@10 vs A | Δ R@10 vs B |
|---------|------|-----|---------|-------------|-------------|
| A — item only | 0.7459 | 0.8276 | 0.6922 | — | −0.019 |
| B — + chunks | **0.7652** | **0.8310** | **0.7038** | **+0.019** | — |
| C — + entity RRF | 0.7470 | 0.8206 | 0.6886 | +0.001 | −0.018 |
| D — + entity passthrough | 0.7581 | 0.7934 | 0.6796 | +0.012 | −0.007 |

**B has the best aggregate on all three metrics.** D (production) beats A but not B. Entity lane (C, D) degrades MRR and NDCG relative to B — the entity regressions push expected articles lower in the ranking even when recall is maintained.

---

## 6. Per-tier breakdown

Mean R@10 per tier:

| Tier | n | A | B | C | D | Best |
|------|---|---|---|---|---|------|
| T1 Exact match | 9 | 1.000 | 1.000 | 1.000 | 1.000 | all equal |
| T2 Lexical cluster | 5 | 0.850 | 0.900 | 0.900 | 0.900 | B/C/D |
| T3 Semantic paraphrase | 6 | 0.861 | 0.917 | 0.833 | 0.875 | B |
| T4 Entity bridge | 6 | 0.722 | 0.694 | 0.708 | 0.708 | A |
| T5 Concept bridge | 7 | 0.560 | 0.679 | 0.690 | 0.690 | C/D |
| T6 Cross-domain synthesis | 12 | 0.575 | 0.543 | 0.503 | 0.524 | A |

Interpretation:
- T1: floor effect — all variants find exact-match articles trivially
- T2: chunks fix the "answer buried in body" problem, +5pp; entity lane neutral
- T3: chunks add +5.6pp; entity lane regresses −8.4pp from B, suggesting thematic paraphrase queries attract wrong entities
- T4: entity lane was designed for this tier but A wins — hub entity regressions offset the genuine bridges
- T5: entity and concept bridges work here; C/D outperform A by +13pp
- T6: hardest tier; entity lane loses −2–7pp vs A because complex multi-cluster queries attract more noise entities

---

## 7. Per-category breakdown

Mean R@10 per category:

| Category | n | A | B | C | D | Best |
|----------|---|---|---|---|---|------|
| direct | 9 | 1.000 | 1.000 | 1.000 | 1.000 | all equal |
| chunk_fixed | 5 | 0.850 | 0.900 | 0.900 | 0.900 | B/C/D |
| multi_hop_pass | 5 | 0.917 | 1.000 | 0.917 | 1.000 | B/D |
| multi_hop_fail | 2 | 0.750 | 0.750 | 0.875 | 0.875 | C/D |
| entity_wins | 3 | 0.778 | 0.722 | 0.833 | 0.833 | C/D |
| entity_regression | 2 | 0.625 | 0.583 | 0.292 | 0.292 | A |
| entity_partial | 2 | 0.700 | 0.650 | 0.600 | 0.600 | A |
| concept_bridge | 3 | 0.444 | 0.722 | 0.556 | 0.556 | B |
| cross_cluster_split | 5 | 0.434 | 0.450 | 0.450 | 0.450 | B/C/D |
| score_displacement | 3 | 0.667 | 0.583 | 0.583 | 0.583 | A |
| vocab_frame_mismatch | 5 | 0.650 | 0.650 | 0.567 | 0.617 | A/B |

The `entity_wins` category confirms the entity lane works as designed when entities are precise. The `entity_regression` category confirms the known failure mode. `concept_bridge` being won by B (not C/D) reflects that concept entities are extracted but don't pass the similarity gate for abstract queries.

---

## 8. Full per-query table

R@10 per variant. **Bold** = best for that query. `—` in best column = all four tied.

| T | Category | Query key | Query | A | B | C | D | Best |
|---|----------|-----------|-------|---|---|---|---|------|
| 1 | direct | ai_labor_direct | AI employment China labor market | 1.00 | 1.00 | 1.00 | 1.00 | — |
| 1 | direct | bad_bunny_direct | Bad Bunny Super Bowl halftime show | 1.00 | 1.00 | 1.00 | 1.00 | — |
| 1 | direct | californian_ideology_direct | Californian ideology technology utopianism | 1.00 | 1.00 | 1.00 | 1.00 | — |
| 1 | direct | context_engineering_direct | context engineering for agents | 1.00 | 1.00 | 1.00 | 1.00 | — |
| 1 | direct | fde_role_direct | forward deployed engineer role skills | 1.00 | 1.00 | 1.00 | 1.00 | — |
| 1 | direct | harness_design_direct | eval harness design long-running AI applications | 1.00 | 1.00 | 1.00 | 1.00 | — |
| 1 | direct | jayz_music_direct | Jay-Z Reasonable Doubt hip-hop album review | 1.00 | 1.00 | 1.00 | 1.00 | — |
| 1 | direct | mlops_tools_direct | MLOps tools and platforms for machine learning deployment | 1.00 | 1.00 | 1.00 | 1.00 | — |
| 1 | direct | ozempic_direct | Ozempic GLP-1 drug effects addiction treatment | 1.00 | 1.00 | 1.00 | 1.00 | — |
| 2 | chunk_fixed | alignment_safety | AI safety alignment and interpretability research | 0.75 | **1.00** | **1.00** | **1.00** | B/C/D |
| 2 | chunk_fixed | inflation_direct | US inflation consumer prices economic data | 1.00 | 1.00 | 1.00 | 1.00 | — |
| 2 | chunk_fixed | rlhf_alignment_technical | reinforcement learning from human feedback reward model training | 1.00 | 1.00 | 1.00 | 1.00 | — |
| 2 | chunk_fixed | system_design_direct | system design interview preparation distributed systems | 1.00 | 1.00 | 1.00 | 1.00 | — |
| 2 | chunk_fixed | trustworthy_agents_security | prompt injection attacks on AI agents security | 0.50 | 0.50 | 0.50 | 0.50 | — |
| 3 | vocab_frame_mismatch | ai_agent_autonomy | autonomous AI agents making decisions without human oversight | **0.75** | 0.50 | 0.25 | 0.25 | **A** |
| 3 | multi_hop_pass | ai_economics_cluster | what does AI mean for workers and economic inequality | 0.75 | **1.00** | 0.75 | **1.00** | B/D |
| 3 | vocab_frame_mismatch | attention_distraction_tech | how technology hijacks attention and makes focus harder | 0.67 | **1.00** | **1.00** | **1.00** | B/C/D |
| 3 | multi_hop_pass | music_algorithm_culture | streaming music algorithms eroding authentic discovery | 1.00 | 1.00 | 1.00 | 1.00 | — |
| 3 | vocab_frame_mismatch | platform_decay_critique | how internet platforms have gotten worse over time | 1.00 | 1.00 | 1.00 | 1.00 | — |
| 3 | multi_hop_pass | teen_culture_identity | youth culture identity and belonging in contemporary America | 1.00 | 1.00 | 1.00 | 1.00 | — |
| 4 | entity_regression | anthropic_claude_products | Anthropic Claude model product line | **0.50** | **0.50** | 0.25 | 0.25 | A/B |
| 4 | entity_wins | chatgpt_work_impact | ChatGPT and AI tools changing how people work | 0.33 | 0.67 | **1.00** | **1.00** | C/D |
| 4 | entity_partial | cnn_cross_domain_bridge | CNN news network business coverage | **1.00** | 0.50 | 0.50 | 0.50 | **A** |
| 4 | multi_hop_pass | palantir_anduril_fde | Palantir Anduril defense tech companies hiring | 1.00 | 1.00 | 1.00 | 1.00 | — |
| 4 | multi_hop_pass | spotify_platform_business | Spotify business model and artist compensation | 1.00 | 1.00 | 1.00 | 1.00 | — |
| 4 | entity_partial | ted_turner_cnn_empire | Ted Turner television empire | 0.50 | 0.50 | 0.50 | 0.50 | — |
| 5 | entity_wins | ai_content_quality_decline | decline of content quality due to AI generated slop | 0.33 | 0.33 | **0.67** | **0.67** | C/D |
| 5 | concept_bridge | enshittification_platforms | platforms getting worse betraying users for profit | 0.75 | 0.75 | 0.75 | 0.75 | — |
| 5 | concept_bridge | human_ai_collaboration_models | different models of humans and AI working together | 0.25 | **0.75** | 0.25 | 0.25 | **B** |
| 5 | concept_bridge | reverse_centaur_ai_work | AI systems directing human workers in task execution | 0.33 | **0.67** | **0.67** | **0.67** | B/C/D |
| 5 | multi_hop_fail | tech_culture_critique | critique of Silicon Valley tech optimism and platform culture | 0.75 | 0.75 | **1.00** | **1.00** | C/D |
| 5 | vocab_frame_mismatch | wellbeing_productivity | mindfulness wellbeing and efficiency in modern work life | 0.75 | 0.75 | 0.75 | 0.75 | — |
| 5 | multi_hop_fail | wellbeing_tech_criticism | how technology platforms undermine human wellbeing | 0.75 | 0.75 | 0.75 | 0.75 | — |
| 6 | score_displacement | agent_observability | observability and debugging for agentic AI systems | 0.50 | 0.50 | 0.50 | 0.50 | — |
| 6 | entity_regression | agent_productivity_reliability | productivity versus reliability tradeoffs using AI assistants | **0.75** | 0.50 | 0.25 | 0.50 | **A** |
| 6 | entity_wins | ai_agent_vs_content_culture | how AI recommendation and agent systems affect culture | 0.00 | 0.25 | **0.50** | **0.50** | C/D |
| 6 | cross_cluster_split | ai_tools_creative_workers | impact of AI tools on creative professionals | 0.40 | **0.60** | 0.40 | 0.40 | **B** |
| 6 | vocab_frame_mismatch | anthropic_products_direct | Anthropic Claude AI model capabilities | **0.67** | **0.67** | 0.33 | 0.33 | A/B |
| 6 | cross_cluster_split | drug_treatment_health_policy | novel drug treatments and health policy in America | **0.67** | 0.33 | 0.33 | 0.33 | **A** |
| 6 | score_displacement | fde_ai_economy | forward deployed engineers and the emerging AI economy | **1.00** | **1.00** | 0.80 | 0.80 | A/B |
| 6 | vocab_frame_mismatch | leadership_culture_sport | leadership culture and team building in American sports and business | 0.67 | 0.67 | 0.67 | 0.67 | — |
| 6 | score_displacement | long_running_agents | how to build reliable long-running AI agents | **0.75** | 0.50 | 0.50 | 0.50 | **A** |
| 6 | cross_cluster_split | ml_engineering_tools | machine learning engineering tools and developer workflow | 0.25 | 0.25 | **0.50** | **0.50** | C/D |
| 6 | vocab_frame_mismatch | music_culture_identity | music as cultural expression and identity in America | 0.75 | 0.75 | 0.75 | 0.75 | — |
| 6 | cross_cluster_split | tech_labor_silicon_valley | how Silicon Valley labor practices shape American work culture | 0.50 | 0.50 | 0.50 | 0.50 | — |

**Variant win count** (uniquely best, excluding ties):

| Variant | Unique wins | Tied-best |
|---------|------------|-----------|
| A | 7 | 31 |
| B | 3 | 19 |
| C/D together | 0 | 10 |

---

## 9. Entity bridge wins

These are the 4 queries where C or D outperforms both A and B — the scenarios where the entity graph provides irreplaceable value.

### `chatgpt_work_impact` — A=0.33, B=0.67, C=1.00, D=1.00 (+0.33 vs B)

Query: "ChatGPT and AI tools changing how people work"
Expected: `management_ai_superpower`, `efficiency_humanity`, `year_in_slop`

`management_ai_superpower` and `efficiency_humanity` share no lexical overlap with `year_in_slop`. TOOL:ChatGPT (sim=0.66 to query) appears in all three articles and bridges the vocabulary gap. The entity lane adds the third article that B cannot find via chunk similarity.

### `tech_culture_critique` — A=0.75, B=0.75, C=1.00, D=1.00 (+0.25 vs B)

Query: "critique of Silicon Valley tech optimism and platform culture"
Expected: `californian_ideology`, `resonant_computing_manifesto`, `banality_recommendation`, `llms_slot_machines`

`llms_slot_machines` uses slot-machine/dopamine vocabulary absent from the other three. CONCEPT:`enshittification` (sim=0.52) appears in both `llms_slot_machines` and `californian_ideology`, providing the bridge. Entity lane recovers the fourth article.

### `ai_agent_vs_content_culture` — A=0.00, B=0.25, C=0.50, D=0.50 (+0.25 vs B)

Query: "how AI recommendation and agent systems affect culture"
Expected: `banality_recommendation`, `llms_slot_machines`, `why_quit_spotify`, `californian_ideology`

Zero items at A — none of the four articles use "AI recommendation and agent systems" vocabulary. B recovers 1 via chunks. C/D recover 2 via entity bridges (`banality_recommendation` via `enshittification`, `llms_slot_machines` via adjacent concept entities). Still missing 2 of 4.

### `ai_content_quality_decline` — A=0.33, B=0.33, C=0.67, D=0.67 (+0.34 vs B)

Query: "decline of content quality due to AI generated slop"
Expected: `year_in_slop`, `llms_slot_machines`, `banality_recommendation`

CONCEPT:`Slop` (extracted by v2 prompt from `year_in_slop`) has sim=0.61 to the query. Entity lane bridges to `llms_slot_machines` via the shared `Slop` entity that B cannot find through chunk similarity alone.

---

## 10. Entity regressions

These are the 5 queries where C or D is worst — the entity lane displaces correct results.

### `ai_agent_autonomy` — A=0.75, B=0.50, C=0.25, D=0.25 (worst: −0.50 vs A)

Query: "autonomous AI agents making decisions without human oversight"
Expected: `why_context_engineering`, `trustworthy_agents`, `lecture_06_initialize`, `building_agents_sdk`

Entity gate passes at sim=0.506 on `Jagged Frontier of AI ability` — a CONCEPT that exists in only `management_ai_superpower`. The top 6 entity-matched entities all point to wrong articles. The correct entity paths exist but score below threshold: `subagents` (sim=0.401 → `building_agents_sdk`), `Deep Research Agent` (0.359 → `why_context_engineering`). Correct entities score lower than wrong entities because the expected articles use *implementation vocabulary* (SDK names, context window) while the query uses *conceptual/oversight vocabulary*.

Root cause: entity vocabulary mismatch. No threshold or cap change can fix this — it requires extraction of conceptual entities from the expected articles (`agent oversight`, `human-in-the-loop`).

### `anthropic_products_direct` — A=0.67, B=0.67, C=0.33, D=0.33 (−0.34 vs A/B)

Query: "Anthropic Claude AI model capabilities"
Expected: `what_is_claude`, `anthropic_sdk_python`, `anthropic_institute_focus`

Five Claude-family entities (`Claude` sim=0.631, `Claude.ai` 0.616, `Claude Agent SDK` 0.589, `Claude Code` 0.576, `Claude Opus 4.6` 0.489) collectively inject 10 articles from the agent engineering cluster into the entity lane. `what_is_claude` appears at entity lane rank 5; `anthropic_sdk_python` and `anthropic_institute_focus` are not reachable via any high-sim entity path.

Root cause: Claude-family entity fragmentation. Five variants × different article sets = scattered signal with no single entity pointing to all expected targets.

### `anthropic_claude_products` — A=0.50, B=0.50, C=0.25, D=0.25 (−0.25 vs A/B)

Query: "Anthropic Claude model product line"
Expected: `what_is_claude`, `anthropic_sdk_python`, `anthropic_institute_focus`, `learn_claude_code`

Same Claude-family fragmentation. `Claude Code` (sim=0.533, 5 articles) is hub-capped to direct-only but still contributes to `building_agents_sdk`, `management_ai_superpower`, `trustworthy_agents`, `skillopt`, `effective_context_engineering` — none expected.

### `fde_ai_economy` — A=1.00, B=1.00, C=0.80, D=0.80 (−0.20 vs A/B)

Query: "forward deployed engineers and the emerging AI economy"
Expected: `fde_what_are`, `fde_hottest_role`, `fde_what_does_it_take`, `ai_engineer_job_outlook`, `management_ai_superpower`

Entity lane correctly retrieves 4/5 expected. The miss is `management_ai_superpower` — pushed out of top-10 by `notes_ai_labor_china` (reaches via `Works Progress Administration for the AI era` entity). `management_ai_superpower` has no FDE-related entity; it's a semantic-similarity match only.

Root cause: entity extraction gap. The article is narrative/managerial — no FDE concept was extracted.

### `ai_tools_creative_workers` — A=0.40, B=0.60, C=0.40, D=0.40 (−0.20 vs B)

Query: "impact of AI tools on creative professionals"
Expected: `notes_ai_labor_china`, `ai_economics_81k`, `banality_recommendation`, `year_in_slop`, `llms_slot_machines`

Entity lane promotes `anthropic_economic_index` to rank 1 via `Claude.ai` (sim=0.548) and `Anthropic AI Usage Index` — neither relevant to creative workers. Three expected articles (`banality_recommendation`, `year_in_slop`, `llms_slot_machines`) have no entity path to this query: these are cultural criticism articles with no AI-labor-impact entities extracted.

Root cause: entity vocabulary mismatch + missing extraction. Same truncation problem as `banality_recommendation` (0 CONCEPTs in v2).

---

## 11. Hub cap investigation

The existing `_ENTITY_HUB_ARTICLE_CAP = 4` limits 1-hop graph expansion from high-frequency entities. `Claude Code` (5 articles) is demoted to direct-only by this cap.

**Finding: hub cap adjustment does not fix any of the 5 regressions.**

- The `entity_relations` graph has only **80 total edges**. Graph expansion contributes negligibly to results — essentially all scoring comes from direct entity→article mention links.
- Lowering cap from 4→3 was simulated across all 5 regression queries: **zero R@10 change** in all cases.
- The regressions are driven by direct entity→article scoring, not by graph traversal.

Raising the gate threshold from 0.40 to 0.50 was also tested: does not help because most regression queries have a top entity sim above 0.50 (e.g. `ai_agent_autonomy` top entity sim=0.506, barely above). Even if the gate prevented it, the next-best entity would cause the same problem.

**Do not adjust hub cap or gate threshold as a fix for these regressions.** The lever is extraction quality, not retrieval parameters.

---

## 12. Residual failures

Queries below R@10=1.0 on all variants, with root cause classification:

| Query | Query text | Best D | Root cause type | What fixes it |
|-------|-----------|--------|-----------------|---------------|
| trustworthy_agents_security | prompt injection attacks on AI agents security | 0.50 | Corpus gap — security articles not well represented | More ingestion |
| ted_turner_cnn_empire | Ted Turner television empire | 0.50 | Entity gap — CNN removed from `trump_tariffs_news` in v2 | Re-examine extraction |
| cnn_cross_domain_bridge | CNN news network business coverage | 0.50 | Same | Same |
| agent_observability | observability and debugging for agentic AI systems | 0.50 | Score displacement | More specific entity extraction |
| long_running_agents | how to build reliable long-running AI agents | 0.50 | Score displacement | Same |
| anthropic_products_direct | Anthropic Claude AI model capabilities | 0.33 | Entity gap — Claude-family fragmentation | Entity deduplication |
| anthropic_claude_products | Anthropic Claude model product line | 0.25 | Same | Same |
| ai_agent_autonomy | autonomous AI agents making decisions without human oversight | 0.25 | Entity vocabulary mismatch | Longer truncation + conceptual re-extraction |
| drug_treatment_health_policy | novel drug treatments and health policy in America | 0.33 | Vocabulary gap — no entity bridge across health clusters | Entity extraction for `ozempic` ↔ `drug policy` |
| enshittification_platforms | platforms getting worse betraying users for profit | 0.75 | Entity gap — 1 of 4 articles unreachable | Longer truncation |
| wellbeing_productivity | mindfulness wellbeing and efficiency in modern work life | 0.75 | Vocabulary gap — `llms_slot_machines` uses dopamine vocabulary | Longer truncation |
| wellbeing_tech_criticism | how technology platforms undermine human wellbeing | 0.75 | Same | Same |
| tech_labor_silicon_valley | how Silicon Valley labor practices shape American work culture | 0.50 | Vocabulary gap — FDE articles use skills/hiring vocabulary | Entity extraction for labor concepts |
| ai_tools_creative_workers | impact of AI tools on creative professionals | 0.40 | Entity vocabulary mismatch | Longer truncation |
| agent_productivity_reliability | productivity versus reliability tradeoffs using AI assistants | 0.50 | Score displacement | More specific entity extraction |
| human_ai_collaboration_models | different models of humans and AI working together | 0.25 (A) | Concept bridge — `centaur`/`reverse-centaur` not bridging | Entity already extracted; gate sim too low |

---

## 13. Phase 9 decision

**Recommendation: Investigate further — do not ship entity lane as a net improvement yet**

Eval skill criteria for "Ship":
- ✅ No guard-rail regressions (T1 queries stayed 1.0 across all variants)
- ✅ Remaining regressions have documented root causes
- ❌ Primary metric (R@10) improved ≥ 2pp vs baseline: D is +1.2pp vs A, −0.7pp vs B — below 2pp threshold
- ❌ Hypothesis confirmed: partially — 4 genuine wins but 8 regressions cancel aggregate gain

The entity lane is worth keeping in production (D): it enables cross-domain retrieval that is structurally impossible without a graph bridge, and it does beat the item-only baseline (+1.2pp). But it does not beat chunks alone (−0.7pp vs B), meaning the entity lane is currently a mixed addition rather than a clear improvement.

The wins are qualitatively important — the 4 queries where entity lane uniquely succeeds are the hardest cross-domain queries (A=0.00 or 0.33 before entities). The regressions are on queries that were already partially working and get worse due to extraction quality gaps, not algorithmic problems.

---

## 14. Next experiments

In priority order:

**1. Increase text truncation** (`text[:3000]` → `text[:10000]`)

Single-line change in `entity_extraction.py`. This is the fix already identified in the v2 extraction eval for `banality_recommendation` and `mindfulness` producing 0 CONCEPTs. Expected to help: `ai_tools_creative_workers`, `wellbeing_tech_criticism`, `enshittification_platforms`. Re-extract the 5–8 narrative articles and rerun a targeted eval before full re-run.

**2. Claude-family entity deduplication**

`upsert_entity` already deduplicates by case-insensitive exact name match — there is no cross-name deduplication. `Claude` and `Claude Code` are treated as unrelated entities even though they are semantically near-identical for routing. Fix: normalize Claude-variant names to a canonical form at extraction time (e.g., emit `Claude` for all mentions of `Claude Code`, `Claude.ai`, `Claude Opus 4.6`). This concentrates all Claude-article signal into one entity instead of fragmenting it across five.

**Note**: `upsert_entity` does not currently have any deduplication across synonym names — this function would need to be extended or a normalization pre-pass added before calling it.

**3. CNN entity re-examination**

`ted_turner_cnn_empire` and `cnn_cross_domain_bridge` scored higher pre-v2 when CNN appeared in `trump_tariffs_news` ("Richard Fisher told CNN"). Prompt v2 correctly removed it as a quote-attribution entity. Evaluate whether re-adding it as a mention (it is factually correct) improves retrieval or re-introduces noise.
