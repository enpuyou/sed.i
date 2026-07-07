"""
Retrieval eval — pilot dataset (8 cases).

One case per tier, chosen to catch harness bugs fast and confirm each
upgrade works as designed. Run in <10 seconds.

Imported by evals/retrieval/runner.py with dataset_size="pilot".
Full dataset: evals/retrieval/dataset/full.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[3] / "content-queue-backend"))
from tests.evals.retrieval_eval_dataset import ARTICLE_IDS

# Re-export so runner can import uniformly
PILOT_QUERIES = [
    # Tier 1 — regression gate: item embeddings sufficient
    {
        "key": "jayz_music_direct",
        "tier": 1,
        "query": "Jay-Z Reasonable Doubt hip-hop album review",
        "expected_ids": [ARTICLE_IDS["reasonable_doubt_jayz"]],
        "guard": True,  # must stay 1.0 across all variants
    },
    # Tier 2 — chunk fix: what_is_claude buried under product-overview intro
    {
        "key": "alignment_safety",
        "tier": 2,
        "query": "AI safety alignment and interpretability research",
        "expected_ids": [
            ARTICLE_IDS["automated_alignment"],
            ARTICLE_IDS["natural_language_autoencoders"],
            ARTICLE_IDS["anthropic_institute_focus"],
            ARTICLE_IDS["what_is_claude"],
        ],
        "guard": False,
        "note": "A=0.75, B=1.00. Chunk fix confirmed.",
    },
    # Tier 3 — semantic paraphrase: passes at A already
    {
        "key": "platform_decay_critique",
        "tier": 3,
        "query": "how internet platforms have gotten worse over time",
        "expected_ids": [
            ARTICLE_IDS["why_quit_spotify"],
            ARTICLE_IDS["californian_ideology"],
            ARTICLE_IDS["resonant_computing_manifesto"],
            ARTICLE_IDS["banality_recommendation"],
        ],
        "guard": True,
    },
    # Tier 3 — semantic paraphrase: entity lane hurts (regression case)
    {
        "key": "ai_agent_autonomy",
        "tier": 3,
        "query": "autonomous AI agents making decisions without human oversight",
        "expected_ids": [
            ARTICLE_IDS["why_context_engineering"],
            ARTICLE_IDS["trustworthy_agents"],
            ARTICLE_IDS["lecture_06_initialize"],
            ARTICLE_IDS["building_agents_sdk"],
        ],
        "guard": False,
        "note": "A=0.75, B=0.50, C=0.25, D=0.25. Hub fan-out regression — A is best.",
    },
    # Tier 4 — entity bridge: ChatGPT links 3 vocabulary-disjoint articles
    {
        "key": "chatgpt_work_impact",
        "tier": 4,
        "query": "ChatGPT and AI tools changing how people work",
        "expected_ids": [
            ARTICLE_IDS["management_ai_superpower"],
            ARTICLE_IDS["efficiency_humanity"],
            ARTICLE_IDS["year_in_slop"],
        ],
        "guard": False,
        "note": "A=0.33, B=0.67, C=1.00, D=1.00. Confirmed entity win.",
    },
    # Tier 5 — concept bridge: entity lane recovers llms_slot_machines
    {
        "key": "tech_culture_critique",
        "tier": 5,
        "query": "critique of Silicon Valley tech optimism and platform culture",
        "expected_ids": [
            ARTICLE_IDS["californian_ideology"],
            ARTICLE_IDS["resonant_computing_manifesto"],
            ARTICLE_IDS["banality_recommendation"],
            ARTICLE_IDS["llms_slot_machines"],
        ],
        "guard": False,
        "note": "A=B=0.75, C=D=1.00. Concept bridge confirmed.",
    },
    # Tier 6 — cross-domain: entity lane adds +0.25 over chunks
    {
        "key": "ai_agent_vs_content_culture",
        "tier": 6,
        "query": "how AI recommendation and agent systems affect culture",
        "expected_ids": [
            ARTICLE_IDS["banality_recommendation"],
            ARTICLE_IDS["llms_slot_machines"],
            ARTICLE_IDS["why_quit_spotify"],
            ARTICLE_IDS["californian_ideology"],
        ],
        "guard": False,
        "note": "A=0.00, B=0.25, C=0.50, D=0.50. Best cross-cluster improvement.",
    },
    # Tier 6 — hub regression: entity fan-out hurts
    {
        "key": "anthropic_products_direct",
        "tier": 6,
        "query": "Anthropic Claude AI model capabilities",
        "expected_ids": [
            ARTICLE_IDS["what_is_claude"],
            ARTICLE_IDS["anthropic_sdk_python"],
            ARTICLE_IDS["anthropic_institute_focus"],
        ],
        "guard": False,
        "note": "A=B=0.67, C=D=0.33. Hub fan-out regression.",
    },
]
