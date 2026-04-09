"""
Eval dataset for hybrid search.

Contains:
1. A set of articles to seed into the DB (with titles, authors, tags, descriptions,
   full_text, and pre-computed embeddings)
2. A set of eval queries, each with:
   - query text
   - expected_intent: what the classifier should return
   - expected_article_ids: which articles should appear in top-10
   - expected_top1_id: which article should be #1 (optional, for MRR)
   - category: what type of query this is (for per-category reporting)
"""

EVAL_ARTICLES = [
    {
        "key": "pg_essay",
        "title": "How to Do Great Work",
        "author": "Paul Graham",
        "original_url": "https://paulgraham.com/greatwork.html",
        "description": "An essay on curiosity-driven work and doing what matters.",
        "tags": ["essay", "productivity", "startups"],
        "full_text": "The first step is to decide what to work on. The work you choose "
        "needs to have three qualities: it has to be something you have a "
        "natural aptitude for, that you have a deep interest in, and that "
        "offers scope to do great work. The key to doing great work is to "
        "be driven by curiosity rather than ambition...",
    },
    {
        "key": "attention_article",
        "title": "The Attention Economy and How It Exploits You",
        "author": "Nir Eyal",
        "original_url": "https://medium.com/attention-economy",
        "description": "How apps use dopamine loops to capture your attention.",
        "tags": ["psychology", "technology", "attention"],
        "full_text": "Variable reward schedules are the engine of addictive design. "
        "The relationship between dopamine and variable rewards explains "
        "why social media apps are structurally addictive. The notification "
        "bell is not a feature, it is a slot machine lever...",
    },
    {
        "key": "deep_work_review",
        "title": "Deep Work by Cal Newport — A Review",
        "author": "Maria Popova",
        "original_url": "https://brainpickings.org/deep-work-review",
        "description": "Why sustained concentration is rare and valuable.",
        "tags": ["books", "productivity", "focus"],
        "full_text": "Newport argues that the attention economy has made sustained "
        "concentration a scarce skill. Deep work is the ability to focus "
        "without distraction on a cognitively demanding task...",
    },
    {
        "key": "rlhf_paper",
        "title": "Training Language Models with RLHF",
        "author": "Long Ouyang",
        "original_url": "https://arxiv.org/abs/2203.02155",
        "description": "Reinforcement learning from human feedback for LLM alignment.",
        "tags": ["ai", "machine-learning", "rlhf"],
        "full_text": "We fine-tune GPT-3 to follow instructions using reinforcement "
        "learning from human feedback. Our labelers rank model outputs "
        "and we use these rankings to train a reward model...",
    },
    {
        "key": "react_hooks",
        "title": "A Complete Guide to React Hooks",
        "author": "Dan Abramov",
        "original_url": "https://overreacted.io/react-hooks",
        "description": "Understanding useState, useEffect, and custom hooks.",
        "tags": ["react", "javascript", "frontend"],
        "full_text": "Hooks let you use state and other React features without "
        "writing a class. useState returns a stateful value and a "
        "function to update it...",
    },
    {
        "key": "nyt_climate",
        "title": "The Climate Crisis Demands Systemic Change",
        "author": "Elizabeth Kolbert",
        "original_url": "https://nytimes.com/climate-systemic-change",
        "description": "Individual action is not enough to address climate change.",
        "tags": ["climate", "politics", "environment"],
        "full_text": "The scale of the climate crisis means that individual choices "
        "like recycling or driving less, while admirable, are insufficient. "
        "What is needed is systemic policy change...",
    },
    {
        "key": "stoicism_guide",
        "title": "A Practical Guide to Stoicism",
        "author": "Ryan Holiday",
        "original_url": "https://dailystoic.com/practical-guide",
        "description": "How ancient philosophy applies to modern life.",
        "tags": ["philosophy", "stoicism", "self-improvement"],
        "full_text": "Stoicism teaches us to focus on what we can control and accept "
        "what we cannot. Marcus Aurelius wrote in his Meditations about "
        "the importance of present-moment awareness...",
    },
]

# ─────────────────────────────────────────────────────────────
# Eval queries: each tests a different search capability
# ─────────────────────────────────────────────────────────────

EVAL_QUERIES = [
    # ── KEYWORD: short exact terms ──
    {
        "query": "RLHF",
        "expected_intent": "keyword",
        "expected_article_keys": ["rlhf_paper"],
        "expected_top1_key": "rlhf_paper",
        "category": "keyword_exact",
    },
    {
        "query": "react hooks",
        "expected_intent": "keyword",
        "expected_article_keys": ["react_hooks"],
        "expected_top1_key": "react_hooks",
        "category": "keyword_exact",
    },
    {
        "query": '"attention economy"',
        "expected_intent": "keyword",
        "expected_article_keys": ["attention_article", "deep_work_review"],
        "expected_top1_key": "attention_article",
        "category": "keyword_phrase",
    },
    # ── FILTER: author inference ──
    {
        "query": "Paul Graham",
        "expected_intent": "filter",
        "expected_article_keys": ["pg_essay"],
        "expected_top1_key": "pg_essay",
        "category": "filter_author",
    },
    {
        "query": "Dan Abramov",
        "expected_intent": "filter",
        "expected_article_keys": ["react_hooks"],
        "expected_top1_key": "react_hooks",
        "category": "filter_author",
    },
    # ── FILTER: tag inference ──
    {
        "query": "stoicism",
        "expected_intent": "filter",
        "expected_article_keys": ["stoicism_guide"],
        "expected_top1_key": "stoicism_guide",
        "category": "filter_tag",
    },
    # ── FILTER: domain inference ──
    {
        "query": "nytimes.com",
        "expected_intent": "filter",
        "expected_article_keys": ["nyt_climate"],
        "expected_top1_key": "nyt_climate",
        "category": "filter_domain",
    },
    # ── FILTER: operators ──
    {
        "query": "tag:ai",
        "expected_intent": "filter",
        "expected_article_keys": ["rlhf_paper"],
        "expected_top1_key": "rlhf_paper",
        "category": "filter_operator",
    },
    {
        "query": "author:Newport",
        "expected_intent": "filter",
        "expected_article_keys": [],  # No exact author named "Newport" but partial match
        "category": "filter_operator",
    },
    # ── SEMANTIC: natural language questions ──
    {
        "query": "what have I read about habit formation and addiction?",
        "expected_intent": "semantic",
        "expected_article_keys": ["attention_article", "deep_work_review"],
        "category": "semantic_question",
    },
    {
        "query": "why is social media addictive?",
        "expected_intent": "semantic",
        "expected_article_keys": ["attention_article"],
        "expected_top1_key": "attention_article",
        "category": "semantic_question",
    },
    {
        "query": "how do I find meaningful work?",
        "expected_intent": "semantic",
        "expected_article_keys": ["pg_essay"],
        "expected_top1_key": "pg_essay",
        "category": "semantic_question",
    },
    {
        "query": "explain reinforcement learning from human feedback",
        "expected_intent": "semantic",
        "expected_article_keys": ["rlhf_paper"],
        "expected_top1_key": "rlhf_paper",
        "category": "semantic_question",
    },
    # ── HYBRID: conceptual multi-word queries ──
    {
        "query": "attention and focus productivity",
        "expected_intent": "hybrid",
        "expected_article_keys": ["attention_article", "deep_work_review"],
        "category": "hybrid_conceptual",
    },
    {
        "query": "ancient philosophy modern applications",
        "expected_intent": "hybrid",
        "expected_article_keys": ["stoicism_guide"],
        "category": "hybrid_conceptual",
    },
    # ── CROSS-CUTTING: queries that test hybrid advantage ──
    # These are cases where NEITHER pure keyword nor pure semantic alone
    # would get the best results, but together they should.
    {
        "query": "dopamine reward loops",
        "expected_intent": "keyword",  # 3 words, no question
        "expected_article_keys": ["attention_article"],
        "expected_top1_key": "attention_article",
        "category": "cross_cutting",
    },
    {
        "query": "climate policy systemic change individual action",
        "expected_intent": "hybrid",  # 6 words, not a question
        "expected_article_keys": ["nyt_climate"],
        "category": "cross_cutting",
    },
]
