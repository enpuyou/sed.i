"""
Tagging eval dataset.

Each example has:
  - title, description, full_text: article content
  - expected_tags: the ideal tags (used for rubric scoring)
  - forbidden_tags: tags that should NOT appear (too generic / wrong domain)

Scoring is done by an LLM-as-judge that evaluates:
  1. Relevance: are the predicted tags actually about this article?
  2. Specificity: are tags specific enough (not "AI", "Technology")?
  3. Domain match: do tags match the actual domain of the article?
"""

TAGGING_EXAMPLES = [
    {
        "key": "rlhf",
        "title": "Training Language Models to Follow Instructions with RLHF",
        "description": "Reinforcement learning from human feedback applied to GPT-3 alignment.",
        "full_text": (
            "We present InstructGPT, a model trained using reinforcement learning from "
            "human feedback. Human labelers rank model outputs and we train a reward model "
            "on these rankings. We then fine-tune GPT-3 using PPO against the reward model. "
            "InstructGPT models are preferred over GPT-3 despite having far fewer parameters. "
            "RLHF reduces harmful outputs and improves instruction-following."
        ),
        "expected_tags": [
            "LLM alignment",
            "reinforcement learning from human feedback",
            "instruction fine-tuning",
            "reward modeling",
        ],
        "forbidden_tags": ["AI", "Technology", "Machine Learning", "GPT"],
    },
    {
        "key": "sourdough",
        "title": "The Science of Sourdough: Why Fermentation Makes Better Bread",
        "description": "How wild yeast and lactobacillus bacteria create complex bread flavor.",
        "full_text": (
            "Sourdough fermentation involves two main organisms: wild yeast (Saccharomyces cerevisiae) "
            "and lactic acid bacteria (Lactobacillus). The bacteria produce lactic and acetic acids "
            "during the long cold fermentation, which lower the pH and develop flavor complexity. "
            "The Maillard reaction during baking creates the characteristic crust color and toasty "
            "aroma. Autolyse before mixing hydrates the flour and develops gluten structure."
        ),
        "expected_tags": [
            "sourdough fermentation",
            "bread baking science",
            "wild yeast cultivation",
            "Maillard reaction",
        ],
        "forbidden_tags": ["Food", "Cooking", "Recipe", "Technology"],
    },
    {
        "key": "sleep_circadian",
        "title": "Circadian Rhythms and Sleep Architecture",
        "description": "How the body clock regulates sleep stages and optimal sleep timing.",
        "full_text": (
            "The circadian rhythm is controlled by the suprachiasmatic nucleus in the hypothalamus. "
            "Light exposure suppresses melatonin secretion and shifts the phase of the circadian clock. "
            "Sleep architecture cycles through REM and non-REM stages approximately every 90 minutes. "
            "Slow-wave sleep (deep sleep) is most restorative and peaks in the first half of the night. "
            "Chronic sleep restriction reduces delta wave amplitude and impairs memory consolidation."
        ),
        "expected_tags": [
            "circadian rhythm disruption",
            "sleep architecture",
            "melatonin regulation",
            "sleep science",
        ],
        "forbidden_tags": ["Health", "Science", "Sleep", "Biology"],
    },
    {
        "key": "stoic_philosophy",
        "title": "Marcus Aurelius on the Dichotomy of Control",
        "description": "The core Stoic practice of distinguishing what is and isn't in our control.",
        "full_text": (
            "Epictetus taught that some things are up to us and some are not. "
            "What is up to us: our judgements, impulses, desires, and aversions. "
            "What is not up to us: our body, reputation, command, and all external things. "
            "Marcus Aurelius applied this practice systematically in his Meditations, "
            "treating obstacles as opportunities for virtue. The Stoic sage does not wish "
            "for events to happen as they want, but for events to happen as they do, "
            "and to flow with the course of nature."
        ),
        "expected_tags": [
            "Stoic philosophy",
            "dichotomy of control",
            "Meditations Marcus Aurelius",
            "virtue ethics",
        ],
        "forbidden_tags": ["Philosophy", "Self-improvement", "History", "Culture"],
    },
    {
        "key": "venture_capital",
        "title": "How VC Funds Actually Work: LP Economics Explained",
        "description": "The structure of venture capital funds, carried interest, and LP returns.",
        "full_text": (
            "A venture capital fund raises money from limited partners (LPs) — pension funds, "
            "endowments, family offices. The fund manager (GP) invests this capital over a 3-5 year "
            "deployment period. Returns are split: LPs get their capital back first (return of capital), "
            "then profits are split 80/20 between LPs and the GP. The GP's 20% cut is called carried "
            "interest. Management fees (typically 2%) cover fund operations. The J-curve describes "
            "how fund NAV dips before returning as early investments mature."
        ),
        "expected_tags": [
            "venture capital fund structure",
            "carried interest mechanics",
            "LP GP economics",
            "J-curve returns",
        ],
        "forbidden_tags": ["Finance", "Business", "Investing", "Startups"],
    },
    {
        "key": "rust_ownership",
        "title": "Understanding Rust's Ownership Model",
        "description": "How Rust eliminates memory bugs at compile time without garbage collection.",
        "full_text": (
            "Rust enforces memory safety through its ownership system at compile time. "
            "Each value has exactly one owner; when the owner goes out of scope, the value is dropped. "
            "Borrowing allows temporary references: immutable borrows (&T) can coexist, but only one "
            "mutable borrow (&mut T) is allowed at a time. The borrow checker enforces these rules "
            "statically, eliminating use-after-free, double-free, and data race bugs without a "
            "runtime garbage collector."
        ),
        "expected_tags": [
            "Rust ownership model",
            "memory safety without GC",
            "borrow checker",
            "systems programming",
        ],
        "forbidden_tags": ["Programming", "Software", "Technology", "Rust"],
    },
    {
        "key": "documentary_editing",
        "title": "The Art of Documentary Film Editing",
        "description": "How editors shape narrative truth in non-fiction filmmaking.",
        "full_text": (
            "Documentary editing differs fundamentally from narrative editing because the editor "
            "works with captured reality rather than scripted scenes. The editor's primary tool is "
            "juxtaposition — placing two shots together to create meaning that neither contains alone. "
            "Manipulating interview footage raises ethical questions about authorial intent versus "
            "subject truth. The Kuleshov effect is especially powerful in documentary: the same "
            "interview response means different things depending on what precedes it."
        ),
        "expected_tags": [
            "documentary film editing",
            "juxtaposition technique",
            "Kuleshov effect",
            "non-fiction filmmaking",
        ],
        "forbidden_tags": ["Film", "Cinema", "Arts", "Media"],
    },
    {
        "key": "rct_methodology",
        "title": "Why Randomized Controlled Trials Are the Gold Standard",
        "description": "How RCTs eliminate confounding and establish causal inference.",
        "full_text": (
            "Randomization eliminates confounding by distributing both measured and unmeasured "
            "confounders equally across treatment groups. This is the key advantage over observational "
            "studies. The placebo effect requires blinding: participants and often researchers must "
            "not know which treatment group they are in. Intent-to-treat analysis preserves the "
            "benefits of randomization even when participants switch groups. Power calculations "
            "determine the required sample size to detect a meaningful effect size at a given "
            "significance level."
        ),
        "expected_tags": [
            "randomized controlled trials",
            "causal inference methods",
            "confounding elimination",
            "research methodology",
        ],
        "forbidden_tags": ["Science", "Research", "Medicine", "Health"],
    },
]
