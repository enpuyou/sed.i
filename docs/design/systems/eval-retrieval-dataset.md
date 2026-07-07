# Retrieval Eval Dataset

**Date**: 2026-07-06
**Articles**: 50 (8 thematic clusters)
**Queries**: 45 across 6 tiers
**Embeddings model**: text-embedding-3-small (item) + contextual chunk embeddings
**Chunk backfill date**: 2026-07-01
**Eval run date**: 2026-07-06 via `run_retrieval_full.py`
**Source file**: `tests/evals/results/retrieval_full_20260706_215231.json`

All scores are real — measured from the live production DB. No values are estimated.

---

## Four Retrieval Strategies

| Strategy | What's active |
|----------|--------------|
| **S0** | Item-level embeddings only (one 1536-dim vector per article, cosine similarity) |
| **S1** | + Chunk embeddings (one vector per ~350-token passage, RRF-fused with keyword) |
| **S2** | + Entity lane, prompt v2, threshold=0.40 (rank-based RRF, no score passthrough) |
| **S3** | + Score passthrough (IDF-dampened entity score × 0.025 added directly to RRF sum) |

S3 is the current production system (`hybrid_search(mode="full")`).

---

## Tier Taxonomy

| Tier | Name | What it tests |
|------|------|--------------|
| **1** | Exact match | Keyword or item embedding sufficient |
| **2** | Lexical cluster | Chunks fix vocabulary dilution in long articles |
| **3** | Semantic paraphrase | Same concept, different words — embedding alignment |
| **4** | Entity bridge | Named entity (tool/person/org) links disconnected articles |
| **5** | Concept bridge | Abstract CONCEPT node bridges articles with zero shared vocabulary |
| **6** | Cross-domain synthesis | 3+ clusters, zero shared vocabulary — current ceiling |

---

## Aggregate Results (45 queries, 2026-07-06)

| Strategy | Mean R@10 | Mean MRR | Mean NDCG@10 |
|----------|-----------|----------|--------------|
| S0 — item only | 0.7459 | 0.8276 | 0.6922 |
| S1 — + chunks | 0.7652 | 0.8310 | 0.7038 |
| S2 — + entity (rank RRF) | 0.7470 | 0.8206 | 0.6886 |
| S3 — + score passthrough | 0.7581 | 0.7934 | 0.6796 |

**Key finding**: S1 has the highest mean R@10 (0.765) and NDCG@10 (0.704). S2 and S3 add entity-bridge wins but also introduce regressions from hub entity fan-out — the net effect is roughly neutral. S3's MRR regression (0.831 → 0.793) reflects cases where entity score passthrough promotes off-target articles to rank 1.

---

## Complete Query Table (all 45 queries, all 4 strategies)

All scores are Recall@10. `*` marks queries where any strategy differs from S0.

### Tier 1 — Exact Match (9 queries)

| Query key | S0 | S1 | S2 | S3 |
|-----------|----|----|----|----|
| jayz_music_direct | 1.00 | 1.00 | 1.00 | 1.00 |
| ozempic_direct | 1.00 | 1.00 | 1.00 | 1.00 |
| bad_bunny_direct | 1.00 | 1.00 | 1.00 | 1.00 |
| mlops_tools_direct | 1.00 | 1.00 | 1.00 | 1.00 |
| fde_role_direct | 1.00 | 1.00 | 1.00 | 1.00 |
| context_engineering_direct | 1.00 | 1.00 | 1.00 | 1.00 |
| ai_labor_direct | 1.00 | 1.00 | 1.00 | 1.00 |
| californian_ideology_direct | 1.00 | 1.00 | 1.00 | 1.00 |
| harness_design_direct | 1.00 | 1.00 | 1.00 | 1.00 |

**All 9 pass at all strategies.** Regression gate: if any drop below 0.90, investigate immediately.

---

### Tier 2 — Lexical Cluster (5 queries)

| Query key | S0 | S1 | S2 | S3 | Note |
|-----------|----|----|----|----|------|
| system_design_direct | 1.00 | 1.00 | 1.00 | 1.00 | Previously S0=0.50 before chunk backfill |
| inflation_direct | 1.00 | 1.00 | 1.00 | 1.00 | |
| alignment_safety `*` | 0.75 | 1.00 | 1.00 | 1.00 | what_is_claude misses at S0 |
| trustworthy_agents_security `*` | 0.50 | 0.50 | 0.50 | 0.50 | learn_claude_code missing at all strategies |
| rlhf_alignment_technical | 1.00 | 1.00 | 1.00 | 1.00 | |

`alignment_safety`: confirmed chunk fix — S0=0.75, S1=1.00. The `what_is_claude` product overview article has its safety research vocabulary buried in the intro; chunks expose the Constitutional AI / RLHF sections.

`trustworthy_agents_security`: stuck at 0.50. `learn_claude_code` discusses CLAUDE.md and agent workflows, not "prompt injection" or "security" explicitly. Neither chunks nor entity graph bridges this.

---

### Tier 3 — Semantic Paraphrase (6 queries)

| Query key | S0 | S1 | S2 | S3 | Note |
|-----------|----|----|----|----|------|
| music_algorithm_culture | 1.00 | 1.00 | 1.00 | 1.00 | |
| ai_economics_cluster `*` | 0.75 | 1.00 | 0.75 | 1.00 | S2 regression, S3 recovers |
| teen_culture_identity | 1.00 | 1.00 | 1.00 | 1.00 | |
| platform_decay_critique | 1.00 | 1.00 | 1.00 | 1.00 | Enshittification paraphrase handled by semantic |
| ai_agent_autonomy `*` | 0.75 | 0.50 | 0.25 | 0.25 | **Monotonic regression — S0 best** |
| attention_distraction_tech `*` | 0.67 | 1.00 | 1.00 | 1.00 | Chunk fix confirmed |

`ai_agent_autonomy` is a confirmed case where entity lane **hurts**: "autonomous"/"oversight" vocabulary pulls economics/policy articles through entity fan-out. S0 item embedding (0.75) is the best strategy for this query. This shows hub-entity suppression is necessary before S2/S3 can be trusted on paraphrase queries.

---

### Tier 4 — Entity Bridge (6 queries)

| Query key | S0 | S1 | S2 | S3 | Bridging entity | Note |
|-----------|----|----|----|----|-----------------|------|
| chatgpt_work_impact `*` | 0.33 | 0.67 | 1.00 | 1.00 | TOOL:ChatGPT | **Confirmed entity win** |
| cnn_cross_domain_bridge `*` | 1.00 | 0.50 | 0.50 | 0.50 | ORG:CNN | S1 chunk regression; entity doesn't recover |
| ted_turner_cnn_empire `*` | 0.50 | 0.50 | 0.50 | 0.50 | PERSON:Ted Turner | 2-hop path not traversed |
| anthropic_claude_products `*` | 0.50 | 0.50 | 0.25 | 0.25 | ORG:Anthropic | Hub fan-out regression |
| spotify_platform_business | 1.00 | 1.00 | 1.00 | 1.00 | TOOL:Spotify | Already passes at S0 |
| palantir_anduril_fde | 1.00 | 1.00 | 1.00 | 1.00 | ORG:Palantir | Already passes at S0 |

**Confirmed entity win**: `chatgpt_work_impact` — S0=0.33 → S1=0.67 → S2=1.00. TOOL:ChatGPT bridges `management_ai_superpower`, `efficiency_humanity`, and `year_in_slop`. These three articles share no vocabulary; the entity graph is the only connection.

**Entity partial failures**: `cnn_cross_domain_bridge` and `ted_turner_cnn_empire` both fail to retrieve `trump_tariffs_news`. The CNN entity may not be extracted from that article, or the 2-hop traversal (Ted Turner → CNN → trump_tariffs_news) is not implemented. Needs investigation.

**Hub regression**: `anthropic_claude_products` regresses at S2/S3 — the Claude-family entity hub (multiple model variants × many articles) fills the top-10 with off-target articles.

---

### Tier 5 — Concept Bridge (7 queries)

| Query key | S0 | S1 | S2 | S3 | Note |
|-----------|----|----|----|----|------|
| wellbeing_tech_criticism `*` | 0.75 | 0.75 | 0.75 | 0.75 | llms_slot_machines absent at all |
| tech_culture_critique `*` | 0.75 | 0.75 | 1.00 | 1.00 | **Concept bridge confirmed** |
| ai_content_quality_decline `*` | 0.33 | 0.33 | 0.67 | 0.67 | **Concept bridge confirmed** |
| wellbeing_productivity `*` | 0.75 | 0.75 | 0.75 | 0.75 | how_organised_2025 absent at all |
| reverse_centaur_ai_work `*` | 0.33 | 0.67 | 0.67 | 0.67 | Concept partially works |
| enshittification_platforms `*` | 0.75 | 0.75 | 0.75 | 0.75 | 3/4 retrieved; one still missing |
| human_ai_collaboration_models `*` | 0.25 | 0.75 | 0.25 | 0.25 | **S2 regression vs S1** |

**Two confirmed concept bridges** (S2 ≥ S1):
- `tech_culture_critique`: S0=S1=0.75, S2=S3=1.00. Entity lane recovers `llms_slot_machines` via a CONCEPT node linking tech-optimism critique to slot-machine framing.
- `ai_content_quality_decline`: S0=S1=0.33, S2=S3=0.67. Entity lane bridges "slop" vocabulary to `llms_slot_machines` or `banality_recommendation`.

**Human-AI collaboration regression**: `human_ai_collaboration_models` goes S0=0.25 → S1=0.75 → S2=0.25. Chunk embeddings correctly capture "humans and AI working together". Entity fan-out on generic "AI"/"agents"/"humans" entities then displaces those articles. The centaur/reverse-centaur parent concept node is not yet bridging `harness_design`.

---

### Tier 6 — Cross-Domain Synthesis (12 queries)

| Query key | S0 | S1 | S2 | S3 | Best | Note |
|-----------|----|----|----|----|------|------|
| ai_agent_vs_content_culture `*` | 0.00 | 0.25 | 0.50 | 0.50 | S2/S3 | Best progression in dataset |
| ai_tools_creative_workers `*` | 0.40 | 0.60 | 0.40 | 0.40 | S1 | S2 regression from hub fan-out |
| tech_labor_silicon_valley | 0.50 | 0.50 | 0.50 | 0.50 | S0 | Stuck — no entity bridge |
| agent_productivity_reliability `*` | 0.75 | 0.50 | 0.25 | 0.50 | S0 | Monotonic regression; S0 best |
| leadership_culture_sport | 0.67 | 0.67 | 0.67 | 0.67 | S0 | tim_cook_interview missing |
| drug_treatment_health_policy `*` | 0.67 | 0.33 | 0.33 | 0.33 | S0 | Chunk regression; S0 best |
| music_culture_identity | 0.75 | 0.75 | 0.75 | 0.75 | S0 | ted_turner_braves missing |
| fde_ai_economy `*` | 1.00 | 1.00 | 0.80 | 0.80 | S0/S1 | Entity fan-out regression |
| long_running_agents `*` | 0.75 | 0.50 | 0.50 | 0.50 | S0 | Chunk score displacement |
| agent_observability | 0.50 | 0.50 | 0.50 | 0.50 | S0 | Vocabulary gap at all strategies |
| anthropic_products_direct `*` | 0.67 | 0.67 | 0.33 | 0.33 | S0/S1 | Hub fan-out regression |
| ml_engineering_tools `*` | 0.25 | 0.25 | 0.50 | 0.50 | S2/S3 | Entity bridge helps |

**Two notable patterns**:

`ai_agent_vs_content_culture` shows the best improvement trajectory in the dataset: S0=0.00, S1=0.25, S2=0.50. Entity lane adds a genuine cross-cluster bridge — CONCEPT:algorithmic_recommendation links the technical and culture clusters. Still only 2/4 articles.

Four queries where **S0 outperforms all later strategies** (`agent_productivity_reliability`, `drug_treatment_health_policy`, `long_running_agents`, `fde_ai_economy`): these are chunk score displacement or hub entity fan-out cases. Entity lane degrades recall rather than improving it.

---

## Failure Root Cause Summary

| Cause | Count | Queries | What fixes it |
|-------|-------|---------|---------------|
| Already passes all strategies | 16 | Tier 1 mostly | Regression gate |
| Chunk fix confirmed | 2 | alignment_safety, attention_distraction_tech | Chunks ✓ |
| Entity bridge confirmed | 3 | chatgpt_work_impact, tech_culture_critique, ai_content_quality_decline | Entity lane ✓ |
| Hub entity fan-out regression | 5 | anthropic_claude_products, anthropic_products_direct, fde_ai_economy, ai_tools_creative_workers, ai_agent_autonomy | Hub cap (skip entities with article_count > N) |
| Score displacement (chunks) | 3 | long_running_agents, drug_treatment_health_policy, cnn_cross_domain_bridge | Score normalization |
| Vocabulary gap — no bridge | 9 | Various tier 5-6 | CONCEPT nodes: personal_productivity, platform_harm, agent_reliability, agent_debugging |
| Entity 2-hop not traversed | 2 | ted_turner_cnn_empire, cnn_cross_domain_bridge | Graph expansion: Ted Turner → CNN → article |

---

## Chunk Backfill (2026-07-01): 22 Articles

| Article | Chunks generated |
|---------|-----------------|
| anthropic_institute_focus | 9 |
| anthropic_sdk_python | 4 |
| automated_alignment | 9 |
| cnn_tensorflow | 11 |
| efficiency_humanity | 2 |
| how_organised_2025 | 10 |
| management_ai_superpower | 7 |
| mindfulness_productivity | 6 |
| natural_language_autoencoders | 6 |
| ozempic_addiction | 14 |
| rare_condition_music | 6 |
| reasonable_doubt_jayz | 13 |
| system_design_hello | 29 |
| ted_turner_braves | 2 |
| textedit_simple_software | 3 |
| tim_cook_interview | 19 |
| too_much_good_taste | 5 |
| what_is_claude | 25 |
| us_inflation_april | 4 |
| interview_prep_google | 15 |
| trump_tariffs_news | 4 |
| elizabeth_warren_democrats | 2 |

---

## How to Re-run

```bash
# Full 4-strategy eval (45 queries × 4 strategies, ~30s)
cd content-queue-backend
PYENV_VERSION=3.11.12 pyenv exec poetry run python tests/evals/run_retrieval_full.py

# Regression gate only (pytest, fast)
PYENV_VERSION=3.11.12 pyenv exec poetry run pytest tests/evals/test_retrieval_evals.py -v

# With printed baseline table
PYENV_VERSION=3.11.12 pyenv exec poetry run pytest tests/evals/test_retrieval_evals.py -v -s
```

After any retrieval change, update `s3_final` values in `retrieval_eval_dataset.py` with the new run's output.

---

## Open Work

1. **Hub cap investigation**: test `entity_article_count > 20 → skip entity` on the 5 hub-regression cases
2. **CNN/Ted Turner 2-hop**: investigate why `trump_tariffs_news` is not reached via entity graph
3. **CONCEPT node gaps**: `personal_productivity`, `platform_harm`, `agent_reliability`, `agent_debugging` nodes not yet bridging the relevant article pairs
4. **Centaur/reverse-centaur parent node**: `human_ai_collaboration_models` query shows the parent concept node is not yet linking `harness_design` — needs explicit extraction or manual creation
