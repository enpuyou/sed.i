"""
Retrieval eval dataset — 50 real article IDs from the production DB (enpu@example.com).

All articles were chunk-embedded as of 2026-07-01 via generate_chunk_embeddings().

Scores across four retrieval strategies (measured 2026-07-06):
  s0_item:    Item-level embeddings only (one 1536-dim vector per article, no chunks)
  s1_chunks:  + Chunk embeddings RRF-fused with keyword (one vector per ~350-token passage)
  s2_entity:  + Entity lane, prompt v2, threshold=0.40 (rank-based RRF, no passthrough)
  s3_final:   + Score passthrough (IDF-dampened entity score × 0.025 added to RRF sum)

All four scores are measured from live production DB runs via run_retrieval_full.py.
No scores are estimated or made up. Source: results/retrieval_full_20260706_215231.json

Query tiers (what each retrieval upgrade is designed to solve):
  Tier 1 — EXACT MATCH       Keyword or item embedding sufficient
  Tier 2 — LEXICAL CLUSTER   Chunks fix vocabulary dilution in long articles
  Tier 3 — SEMANTIC PARAPHRASE  Same concept, different words — embedding alignment
  Tier 4 — ENTITY BRIDGE     Named entity (tool/person/org) links articles
  Tier 5 — CONCEPT BRIDGE    Abstract concept node bridges unlike vocabulary
  Tier 6 — CROSS-DOMAIN      Multi-cluster synthesis; current ceiling
"""

# ─────────────────────────────────────────────────────────────
# 50-article map, 8 thematic clusters
# ─────────────────────────────────────────────────────────────

ARTICLE_IDS = {
    # AI agents / engineering
    "skillopt": "7e429d52-6caf-4b4f-a870-55641009040d",
    "why_context_engineering": "21563b09-a20d-4708-8388-59adb27a3ea7",
    "harness_design": "dabb188e-a40c-4bb1-8fa8-f74bf715bc11",
    "effective_context_engineering": "0f2afda5-30a9-4061-b0ac-ea688a9d66b0",
    "lecture_06_initialize": "a749843d-fba2-440e-89ad-43c7452c344d",
    "lecture_11_observable": "ec39ee77-c8d4-4041-b4f0-ecb66e38c7c1",
    "trustworthy_agents": "7bd69c0d-84b9-42b3-acef-6fea78452fb0",
    "building_agents_sdk": "2147eb04-912f-4054-9f0a-e4030b94cdaa",
    "learn_claude_code": "982bc5f5-c2b4-497b-8bb5-5a9ffed14fee",
    # Anthropic products / research
    "what_is_claude": "1e45fecb-a28c-42f5-a288-ab1ad76f6602",
    "anthropic_sdk_python": "4dec975d-9ace-49f2-8400-81e09b35fca9",
    "automated_alignment": "d52c8874-8db6-4feb-a111-8a09dbe8c484",
    "natural_language_autoencoders": "e8d39f0c-f275-4a99-aa89-a6a380a79da7",
    "anthropic_institute_focus": "fb043061-1fa5-4040-8f1e-69699cf49a2b",
    "mlops_tools_2026": "51eeac17-f89e-4fc8-96a9-ea00c48db470",
    # AI labor / economics
    "notes_ai_labor_china": "5c90340b-0bb5-477e-a805-b514a96e597c",
    "ai_economics_81k": "4ca2535e-ebdc-415e-866a-8ac2286f5ed6",
    "anthropic_economic_index": "4e621507-6614-4867-b16b-9f59ac9cad4c",
    "ai_engineer_job_outlook": "14472304-12ad-435b-9240-01563b143bfc",
    "management_ai_superpower": "1eb2bac8-584c-4f87-80f7-79ec7145b7aa",
    "ai_shadow_investments": "89f26e77-c835-44c8-aff4-a40a3b93770a",
    # Forward deployed engineers
    "fde_what_are": "6ab8be00-9d40-4a64-8947-9b16d25f1098",
    "fde_hottest_role": "b539a14e-5d6e-4e6e-a388-4e591f57d117",
    "fde_what_does_it_take": "dc11ffd1-3397-426f-9e91-aa6f20dba7b3",
    # Digital culture / recommendation criticism
    "banality_recommendation": "e695868e-9ac9-4645-a55c-ab208dfee29a",
    "why_quit_spotify": "047fa3cc-5982-4ddb-bacd-897ea288acc5",
    "resonant_computing_manifesto": "dc648306-6a3d-4cfa-9852-127f22d17980",
    "californian_ideology": "89bb5f33-e7fd-424a-a569-caf6ed3093de",
    "year_in_slop": "ff3cec57-7cff-4670-aaf8-0817a47218e8",
    "llms_slot_machines": "34b626c0-61fd-4ee4-b39d-0e6edb561b6d",
    "too_much_good_taste": "481469a6-b99a-44ef-8d58-6a48d4e6e0a9",
    "textedit_simple_software": "2c8210b9-718d-4d64-b9c1-cc9ca472aa10",
    # Wellbeing / personal productivity
    "how_organised_2025": "6ec5aed1-8ee6-4f75-bdbb-81c955762e90",
    "ozempic_addiction": "a9bbf043-9f4a-4c29-975e-6ff28e79a74e",
    "efficiency_humanity": "1abefec1-4047-4ae2-ba45-b30f8b8e57c5",
    "mindfulness_productivity": "3c7c122b-8928-4b84-814d-6cb14f330e72",
    "why_ordinary": "df8a9886-6def-4d6c-8bf0-05d95e61150d",
    "fertility_declining": "6f4d33a3-efce-48e8-8e10-a29b69a9396f",
    # Music / American culture
    "bad_bunny_allamerican": "1b3d04ba-b9fc-456f-bc7b-ede5c75a8d50",
    "bad_bunny_super_bowl": "de9152e7-0be9-4ed8-bdf2-3341fe85160c",
    "reasonable_doubt_jayz": "0820a925-6560-42c9-9e3b-42a02a8ca8ba",
    "rare_condition_music": "418dced5-d30c-44a7-9faf-1b1ea2467d10",
    "ted_turner_braves": "d840fd28-6af9-4c0d-85df-8560b83b8f55",
    "tim_cook_interview": "ee5af008-6e76-4f97-9954-dc3e81bade41",
    # ML engineering / technical
    "system_design_hello": "0c3ce28b-38c7-4c4e-9f16-32b2f4ba5ef9",
    "cnn_tensorflow": "b638e930-2fb4-459d-9db2-b5b29bfb5a6f",
    # Current events / policy
    "us_inflation_april": "9430a93e-a8f4-4df4-bad0-c47df8c914af",
    "interview_prep_google": "98c5473d-201f-457c-8766-9d76e7547a72",
    "trump_tariffs_news": "f4125333-8750-4a55-88c3-96c876d17181",
    "elizabeth_warren_democrats": "bc9609b4-55ee-4ba9-b17e-aacd3bc8ffe5",
}


# ─────────────────────────────────────────────────────────────
# Retrieval eval queries — tiered by complexity
#
# All scores measured 2026-07-06 via run_retrieval_full.py against
# production DB (enpu@example.com). Source file:
#   results/retrieval_full_20260706_215231.json
# ─────────────────────────────────────────────────────────────

RETRIEVAL_EVAL_QUERIES = [
    # ══════════════════════════════════════════════════════════
    # TIER 1 — EXACT MATCH
    # ══════════════════════════════════════════════════════════
    {
        "key": "jayz_music_direct",
        "tier": 1,
        "query": "Jay-Z Reasonable Doubt hip-hop album review",
        "expected_ids": ["reasonable_doubt_jayz"],
        "s0_item": 1.00,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": False,
        "category": "direct",
    },
    {
        "key": "ozempic_direct",
        "tier": 1,
        "query": "Ozempic GLP-1 drug effects addiction treatment",
        "expected_ids": ["ozempic_addiction"],
        "s0_item": 1.00,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": False,
        "category": "direct",
    },
    {
        "key": "bad_bunny_direct",
        "tier": 1,
        "query": "Bad Bunny Super Bowl halftime show",
        "expected_ids": ["bad_bunny_allamerican", "bad_bunny_super_bowl"],
        "s0_item": 1.00,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": False,
        "category": "direct",
    },
    {
        "key": "mlops_tools_direct",
        "tier": 1,
        "query": "MLOps tools and platforms for machine learning deployment",
        "expected_ids": ["mlops_tools_2026"],
        "s0_item": 1.00,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": False,
        "category": "direct",
    },
    {
        "key": "fde_role_direct",
        "tier": 1,
        "query": "forward deployed engineer role skills",
        "expected_ids": ["fde_what_are", "fde_hottest_role", "fde_what_does_it_take"],
        "s0_item": 1.00,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": False,
        "category": "direct",
    },
    {
        "key": "context_engineering_direct",
        "tier": 1,
        "query": "context engineering for agents",
        "expected_ids": [
            "effective_context_engineering",
            "why_context_engineering",
            "trustworthy_agents",
        ],
        "s0_item": 1.00,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": False,
        "category": "direct",
    },
    {
        "key": "ai_labor_direct",
        "tier": 1,
        "query": "AI employment China labor market",
        "expected_ids": ["notes_ai_labor_china", "ai_economics_81k"],
        "s0_item": 1.00,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": False,
        "category": "direct",
    },
    {
        "key": "californian_ideology_direct",
        "tier": 1,
        "query": "Californian ideology technology utopianism",
        "expected_ids": ["californian_ideology"],
        "s0_item": 1.00,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": False,
        "category": "direct",
    },
    {
        "key": "harness_design_direct",
        "tier": 1,
        "query": "eval harness design long-running AI applications",
        "expected_ids": ["harness_design"],
        "s0_item": 1.00,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": False,
        "category": "direct",
    },
    # ══════════════════════════════════════════════════════════
    # TIER 2 — LEXICAL CLUSTER
    # ══════════════════════════════════════════════════════════
    {
        "key": "system_design_direct",
        "tier": 2,
        "query": "system design interview preparation distributed systems",
        "expected_ids": ["system_design_hello", "interview_prep_google"],
        "s0_item": 1.00,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": False,
        "category": "chunk_fixed",
        "note": "Both articles surface at S0 — item embeddings now sufficient after "
        "chunk backfill updated the item embedding context.",
    },
    {
        "key": "inflation_direct",
        "tier": 2,
        "query": "US inflation consumer prices economic data",
        "expected_ids": ["us_inflation_april", "notes_ai_labor_china"],
        "s0_item": 1.00,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": False,
        "category": "chunk_fixed",
    },
    {
        "key": "alignment_safety",
        "tier": 2,
        "query": "AI safety alignment and interpretability research",
        "expected_ids": [
            "automated_alignment",
            "natural_language_autoencoders",
            "anthropic_institute_focus",
            "what_is_claude",
        ],
        "s0_item": 0.75,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": True,
        "category": "chunk_fixed",
        "note": "what_is_claude misses at S0 (item embedding = product overview framing). "
        "Chunks expose Constitutional AI / RLHF sections → S1 fixes.",
    },
    {
        "key": "trustworthy_agents_security",
        "tier": 2,
        "query": "prompt injection attacks on AI agents security",
        "expected_ids": ["trustworthy_agents", "learn_claude_code"],
        "s0_item": 0.50,
        "s1_chunks": 0.50,
        "s2_entity": 0.50,
        "s3_final": 0.50,
        "multi_hop": False,
        "category": "vocab_frame_mismatch",
        "note": "learn_claude_code misses at all strategies. Discusses CLAUDE.md and "
        "agent workflows, not 'prompt injection' or 'security' directly.",
    },
    {
        "key": "rlhf_alignment_technical",
        "tier": 2,
        "query": "reinforcement learning from human feedback reward model training",
        "expected_ids": ["automated_alignment", "natural_language_autoencoders"],
        "s0_item": 1.00,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": False,
        "category": "direct",
    },
    # ══════════════════════════════════════════════════════════
    # TIER 3 — SEMANTIC PARAPHRASE
    # ══════════════════════════════════════════════════════════
    {
        "key": "music_algorithm_culture",
        "tier": 3,
        "query": "streaming music algorithms eroding authentic discovery",
        "expected_ids": [
            "why_quit_spotify",
            "banality_recommendation",
            "resonant_computing_manifesto",
            "rare_condition_music",
        ],
        "s0_item": 1.00,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": True,
        "category": "multi_hop_pass",
    },
    {
        "key": "ai_economics_cluster",
        "tier": 3,
        "query": "what does AI mean for workers and economic inequality",
        "expected_ids": [
            "notes_ai_labor_china",
            "ai_economics_81k",
            "anthropic_economic_index",
            "ai_engineer_job_outlook",
        ],
        "s0_item": 0.75,
        "s1_chunks": 1.00,
        "s2_entity": 0.75,
        "s3_final": 1.00,
        "multi_hop": True,
        "category": "multi_hop_pass",
        "note": "S2 regression vs S1: entity lane fan-out displaces one expected article. "
        "S3 recovers via score passthrough rebalancing.",
    },
    {
        "key": "teen_culture_identity",
        "tier": 3,
        "query": "youth culture identity and belonging in contemporary America",
        "expected_ids": [
            "bad_bunny_allamerican",
            "bad_bunny_super_bowl",
            "reasonable_doubt_jayz",
            "too_much_good_taste",
        ],
        "s0_item": 1.00,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": True,
        "category": "multi_hop_pass",
    },
    {
        "key": "platform_decay_critique",
        "tier": 3,
        "query": "how internet platforms have gotten worse over time",
        "expected_ids": [
            "why_quit_spotify",
            "californian_ideology",
            "resonant_computing_manifesto",
            "banality_recommendation",
        ],
        "s0_item": 1.00,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": True,
        "category": "multi_hop_pass",
        "note": "Enshittification paraphrase fully resolved by semantic embedding.",
    },
    {
        "key": "ai_agent_autonomy",
        "tier": 3,
        "query": "autonomous AI agents making decisions without human oversight",
        "expected_ids": [
            "why_context_engineering",
            "trustworthy_agents",
            "lecture_06_initialize",
            "building_agents_sdk",
        ],
        "s0_item": 0.75,
        "s1_chunks": 0.50,
        "s2_entity": 0.25,
        "s3_final": 0.25,
        "multi_hop": True,
        "category": "vocab_frame_mismatch",
        "note": "Regresses monotonically through each upgrade. 'Autonomous'/'oversight' "
        "pulls economics and policy articles via entity fan-out. S0 is the best "
        "strategy (0.75) — entity lane actively hurts this query.",
    },
    {
        "key": "attention_distraction_tech",
        "tier": 3,
        "query": "how technology hijacks attention and makes focus harder",
        "expected_ids": [
            "llms_slot_machines",
            "textedit_simple_software",
            "efficiency_humanity",
        ],
        "s0_item": 0.67,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": True,
        "category": "chunk_fixed",
        "note": "efficiency_humanity misses at S0 ('humanity/burnout' vocab). "
        "Chunks surface cognitive overload passages → S1 fixes.",
    },
    # ══════════════════════════════════════════════════════════
    # TIER 4 — ENTITY BRIDGE
    # ══════════════════════════════════════════════════════════
    {
        "key": "chatgpt_work_impact",
        "tier": 4,
        "query": "ChatGPT and AI tools changing how people work",
        "expected_ids": [
            "management_ai_superpower",
            "efficiency_humanity",
            "year_in_slop",
        ],
        "s0_item": 0.33,
        "s1_chunks": 0.67,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": True,
        "category": "entity_wins",
        "note": "Confirmed entity win. TOOL:ChatGPT bridges 3 thematically disconnected "
        "articles with no shared vocabulary.",
    },
    {
        "key": "cnn_cross_domain_bridge",
        "tier": 4,
        "query": "CNN news network business coverage",
        "expected_ids": ["ted_turner_braves", "trump_tariffs_news"],
        "s0_item": 1.00,
        "s1_chunks": 0.50,
        "s2_entity": 0.50,
        "s3_final": 0.50,
        "multi_hop": True,
        "category": "entity_partial",
        "note": "Unexpected: S0 item embedding finds both articles but S1 chunks regress. "
        "trump_tariffs_news not recovered by entity lane — CNN entity not extracted "
        "from that article or similarity below threshold. Needs investigation.",
    },
    {
        "key": "ted_turner_cnn_empire",
        "tier": 4,
        "query": "Ted Turner television empire",
        "expected_ids": ["ted_turner_braves", "trump_tariffs_news"],
        "s0_item": 0.50,
        "s1_chunks": 0.50,
        "s2_entity": 0.50,
        "s3_final": 0.50,
        "multi_hop": True,
        "category": "entity_partial",
        "note": "trump_tariffs_news not retrieved at any strategy. 2-hop entity path "
        "(Ted Turner → CNN → trump_tariffs_news) not traversed. "
        "Primary entity graph gap to investigate.",
    },
    {
        "key": "anthropic_claude_products",
        "tier": 4,
        "query": "Anthropic Claude model product line",
        "expected_ids": [
            "what_is_claude",
            "anthropic_sdk_python",
            "anthropic_institute_focus",
            "learn_claude_code",
        ],
        "s0_item": 0.50,
        "s1_chunks": 0.50,
        "s2_entity": 0.25,
        "s3_final": 0.25,
        "multi_hop": True,
        "category": "entity_regression",
        "note": "S2/S3 regression: Claude-family entity hub fan-out fills top-10 with "
        "off-target articles. Hub cap needed.",
    },
    {
        "key": "spotify_platform_business",
        "tier": 4,
        "query": "Spotify business model and artist compensation",
        "expected_ids": ["why_quit_spotify", "banality_recommendation"],
        "s0_item": 1.00,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": True,
        "category": "multi_hop_pass",
    },
    {
        "key": "palantir_anduril_fde",
        "tier": 4,
        "query": "Palantir Anduril defense tech companies hiring",
        "expected_ids": ["fde_what_are", "fde_hottest_role", "fde_what_does_it_take"],
        "s0_item": 1.00,
        "s1_chunks": 1.00,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": False,
        "category": "direct",
    },
    # ══════════════════════════════════════════════════════════
    # TIER 5 — CONCEPT BRIDGE
    # ══════════════════════════════════════════════════════════
    {
        "key": "wellbeing_tech_criticism",
        "tier": 5,
        "query": "how technology platforms undermine human wellbeing",
        "expected_ids": [
            "mindfulness_productivity",
            "efficiency_humanity",
            "llms_slot_machines",
            "textedit_simple_software",
        ],
        "s0_item": 0.75,
        "s1_chunks": 0.75,
        "s2_entity": 0.75,
        "s3_final": 0.75,
        "multi_hop": True,
        "category": "multi_hop_fail",
        "note": "llms_slot_machines absent at all strategies. 'Slot machine'/'dopamine loop' "
        "has no shared vocabulary with 'wellbeing'/'technology platforms'. "
        "CONCEPT:platform_harm not bridging these articles.",
    },
    {
        "key": "tech_culture_critique",
        "tier": 5,
        "query": "critique of Silicon Valley tech optimism and platform culture",
        "expected_ids": [
            "californian_ideology",
            "resonant_computing_manifesto",
            "banality_recommendation",
            "llms_slot_machines",
        ],
        "s0_item": 0.75,
        "s1_chunks": 0.75,
        "s2_entity": 1.00,
        "s3_final": 1.00,
        "multi_hop": True,
        "category": "entity_wins",
        "note": "Confirmed concept bridge: S2 entity lane recovers llms_slot_machines "
        "via CONCEPT node linking tech-optimism critique to slot-machine framing.",
    },
    {
        "key": "ai_content_quality_decline",
        "tier": 5,
        "query": "decline of content quality due to AI generated slop",
        "expected_ids": [
            "year_in_slop",
            "llms_slot_machines",
            "banality_recommendation",
        ],
        "s0_item": 0.33,
        "s1_chunks": 0.33,
        "s2_entity": 0.67,
        "s3_final": 0.67,
        "multi_hop": True,
        "category": "entity_wins",
        "note": "Confirmed concept bridge: entity lane bridges 'slop' vocabulary to "
        "llms_slot_machines or banality_recommendation. One article still missing.",
    },
    {
        "key": "wellbeing_productivity",
        "tier": 5,
        "query": "mindfulness wellbeing and efficiency in modern work life",
        "expected_ids": [
            "mindfulness_productivity",
            "efficiency_humanity",
            "how_organised_2025",
            "why_ordinary",
        ],
        "s0_item": 0.75,
        "s1_chunks": 0.75,
        "s2_entity": 0.75,
        "s3_final": 0.75,
        "multi_hop": True,
        "category": "multi_hop_fail",
        "note": "how_organised_2025 absent at all strategies. 'Calendar systems'/'habit "
        "tracking' has no vocabulary overlap with 'mindfulness'/'wellbeing'. "
        "CONCEPT:personal_productivity bridge not yet created.",
    },
    {
        "key": "reverse_centaur_ai_work",
        "tier": 5,
        "query": "AI systems directing human workers in task execution",
        "expected_ids": ["llms_slot_machines", "management_ai_superpower", "skillopt"],
        "s0_item": 0.33,
        "s1_chunks": 0.67,
        "s2_entity": 0.67,
        "s3_final": 0.67,
        "multi_hop": True,
        "category": "concept_bridge",
        "note": "CONCEPT:reverse-centaur partially works: S1/S2/S3 recover one more article "
        "than S0. One article still missing — concept node present but not fully "
        "linking all three expected articles.",
    },
    {
        "key": "enshittification_platforms",
        "tier": 5,
        "query": "platforms getting worse betraying users for profit",
        "expected_ids": [
            "why_quit_spotify",
            "llms_slot_machines",
            "banality_recommendation",
            "californian_ideology",
        ],
        "s0_item": 0.75,
        "s1_chunks": 0.75,
        "s2_entity": 0.75,
        "s3_final": 0.75,
        "multi_hop": True,
        "category": "concept_bridge",
        "note": "3/4 articles retrieved at all strategies. Semantic embedding handles "
        "most of the paraphrase. llms_slot_machines likely the missing article "
        "('dopamine'/'slot machine' vs 'betraying users').",
    },
    {
        "key": "human_ai_collaboration_models",
        "tier": 5,
        "query": "different models of humans and AI working together",
        "expected_ids": [
            "llms_slot_machines",
            "management_ai_superpower",
            "skillopt",
            "harness_design",
        ],
        "s0_item": 0.25,
        "s1_chunks": 0.75,
        "s2_entity": 0.25,
        "s3_final": 0.25,
        "multi_hop": True,
        "category": "concept_bridge",
        "note": "S2/S3 regress sharply vs S1 (0.75 → 0.25). Chunk embeddings correctly "
        "capture 'humans and AI working together'. Entity fan-out (AI, agents, humans) "
        "injects noise displacing relevant articles. Centaur/reverse-centaur parent "
        "concept node not yet present or not linking harness_design.",
    },
    # ══════════════════════════════════════════════════════════
    # TIER 6 — CROSS-DOMAIN SYNTHESIS
    # ══════════════════════════════════════════════════════════
    {
        "key": "ai_agent_vs_content_culture",
        "tier": 6,
        "query": "how AI recommendation and agent systems affect culture",
        "expected_ids": [
            "banality_recommendation",
            "llms_slot_machines",
            "why_quit_spotify",
            "californian_ideology",
        ],
        "s0_item": 0.00,
        "s1_chunks": 0.25,
        "s2_entity": 0.50,
        "s3_final": 0.50,
        "multi_hop": True,
        "category": "cross_cluster_split",
        "note": "Best progression in dataset: 0.00 → 0.25 → 0.50. Entity lane adds "
        "+0.25 over chunks. CONCEPT:algorithmic_recommendation partially bridges "
        "technical and culture clusters. 2/4 articles now retrieved.",
    },
    {
        "key": "ai_tools_creative_workers",
        "tier": 6,
        "query": "impact of AI tools on creative professionals",
        "expected_ids": [
            "notes_ai_labor_china",
            "ai_economics_81k",
            "banality_recommendation",
            "year_in_slop",
            "llms_slot_machines",
        ],
        "s0_item": 0.40,
        "s1_chunks": 0.60,
        "s2_entity": 0.40,
        "s3_final": 0.40,
        "multi_hop": True,
        "category": "cross_cluster_split",
        "note": "S2/S3 regression vs S1: generic 'AI tools' entity hub fan-out displaces "
        "correct articles. S1 (no entity) is best strategy for this query. "
        "Confirms hub-entity suppression needed.",
    },
    {
        "key": "tech_labor_silicon_valley",
        "tier": 6,
        "query": "how Silicon Valley labor practices shape American work culture",
        "expected_ids": [
            "californian_ideology",
            "notes_ai_labor_china",
            "fde_what_are",
            "fde_hottest_role",
        ],
        "s0_item": 0.50,
        "s1_chunks": 0.50,
        "s2_entity": 0.50,
        "s3_final": 0.50,
        "multi_hop": True,
        "category": "cross_cluster_split",
        "note": "Stuck at 0.50 across all strategies. FDE articles use hiring/skills vocab "
        "not 'labor practices'/'work culture'. Entity bridge not yet created.",
    },
    {
        "key": "agent_productivity_reliability",
        "tier": 6,
        "query": "productivity versus reliability tradeoffs using AI assistants",
        "expected_ids": [
            "why_context_engineering",
            "harness_design",
            "trustworthy_agents",
            "effective_context_engineering",
        ],
        "s0_item": 0.75,
        "s1_chunks": 0.50,
        "s2_entity": 0.25,
        "s3_final": 0.50,
        "multi_hop": True,
        "category": "score_displacement",
        "note": "Monotonic regression through S1/S2; S3 partially recovers. "
        "'Productivity'/'AI assistants' pulls economics cluster via entity fan-out. "
        "S0 best at 0.75 — entity lane actively hurts this query.",
    },
    {
        "key": "leadership_culture_sport",
        "tier": 6,
        "query": "leadership culture and team building in American sports and business",
        "expected_ids": [
            "ted_turner_braves",
            "tim_cook_interview",
            "management_ai_superpower",
        ],
        "s0_item": 0.67,
        "s1_chunks": 0.67,
        "s2_entity": 0.67,
        "s3_final": 0.67,
        "multi_hop": True,
        "category": "vocab_frame_mismatch",
        "note": "tim_cook_interview absent at all strategies. Apple supply chain / "
        "product strategy framing — no 'leadership culture' vocabulary.",
    },
    {
        "key": "drug_treatment_health_policy",
        "tier": 6,
        "query": "novel drug treatments and health policy in America",
        "expected_ids": [
            "ozempic_addiction",
            "fertility_declining",
            "us_inflation_april",
        ],
        "s0_item": 0.67,
        "s1_chunks": 0.33,
        "s2_entity": 0.33,
        "s3_final": 0.33,
        "multi_hop": True,
        "category": "cross_cluster_split",
        "note": "S0 best at 0.67 — chunks regress. fertility_declining and us_inflation_april "
        "both miss (demographic / CPI vocab, not 'drug treatments'/'health policy').",
    },
    {
        "key": "music_culture_identity",
        "tier": 6,
        "query": "music as cultural expression and identity in America",
        "expected_ids": [
            "bad_bunny_allamerican",
            "reasonable_doubt_jayz",
            "rare_condition_music",
            "ted_turner_braves",
        ],
        "s0_item": 0.75,
        "s1_chunks": 0.75,
        "s2_entity": 0.75,
        "s3_final": 0.75,
        "multi_hop": True,
        "category": "vocab_frame_mismatch",
        "note": "ted_turner_braves absent at all strategies. 'Atlanta Braves'/'media empire' "
        "framing — no 'music'/'cultural expression' vocabulary.",
    },
    {
        "key": "fde_ai_economy",
        "tier": 6,
        "query": "forward deployed engineers and the emerging AI economy",
        "expected_ids": [
            "fde_what_are",
            "fde_hottest_role",
            "fde_what_does_it_take",
            "ai_engineer_job_outlook",
            "management_ai_superpower",
        ],
        "s0_item": 1.00,
        "s1_chunks": 1.00,
        "s2_entity": 0.80,
        "s3_final": 0.80,
        "multi_hop": True,
        "category": "score_displacement",
        "note": "S2/S3 regression: entity fan-out displaces one expected article. "
        "fde_hottest_role or management_ai_superpower pushed out of top-10.",
    },
    {
        "key": "long_running_agents",
        "tier": 6,
        "query": "how to build reliable long-running AI agents",
        "expected_ids": [
            "why_context_engineering",
            "harness_design",
            "lecture_06_initialize",
            "lecture_11_observable",
        ],
        "s0_item": 0.75,
        "s1_chunks": 0.50,
        "s2_entity": 0.50,
        "s3_final": 0.50,
        "multi_hop": True,
        "category": "score_displacement",
        "note": "S1 regression: lecture_11's chunks dominate score threshold. "
        "CONCEPT:agent_reliability bridge not yet created.",
    },
    {
        "key": "agent_observability",
        "tier": 6,
        "query": "observability and debugging for agentic AI systems",
        "expected_ids": [
            "lecture_11_observable",
            "harness_design",
            "trustworthy_agents",
            "learn_claude_code",
        ],
        "s0_item": 0.50,
        "s1_chunks": 0.50,
        "s2_entity": 0.50,
        "s3_final": 0.50,
        "multi_hop": True,
        "category": "vocab_frame_mismatch",
        "note": "harness_design ('eval harness'/'tracing') and learn_claude_code ('CLAUDE.md') "
        "don't use 'observability'/'debugging'. CONCEPT:agent_debugging bridge needed.",
    },
    {
        "key": "anthropic_products_direct",
        "tier": 6,
        "query": "Anthropic Claude AI model capabilities",
        "expected_ids": [
            "what_is_claude",
            "anthropic_sdk_python",
            "anthropic_institute_focus",
        ],
        "s0_item": 0.67,
        "s1_chunks": 0.67,
        "s2_entity": 0.33,
        "s3_final": 0.33,
        "multi_hop": False,
        "category": "entity_regression",
        "note": "S2/S3 regression: Claude-family entity hub fan-out fills top-10. "
        "anthropic_sdk_python loses its slot to hub-matched off-target articles.",
    },
    {
        "key": "ml_engineering_tools",
        "tier": 6,
        "query": "machine learning engineering tools and developer workflow",
        "expected_ids": [
            "mlops_tools_2026",
            "cnn_tensorflow",
            "system_design_hello",
            "anthropic_sdk_python",
        ],
        "s0_item": 0.25,
        "s1_chunks": 0.25,
        "s2_entity": 0.50,
        "s3_final": 0.50,
        "multi_hop": True,
        "category": "cross_cluster_split",
        "note": "S2 entity improvement: +0.25 vs S0/S1. Entity lane bridges TOOL:TensorFlow "
        "or CONCEPT:ml_tooling across articles. system_design_hello and "
        "anthropic_sdk_python still missing — vocabulary too domain-split.",
    },
]
