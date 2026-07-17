"""
Pilot dataset: 21 research questions grounded in the real dev library.

Each case specifies:
  question         — what the user asks
  category         — structural type of question (see below)

  # Intent-based fields (v2 rubric — used by question_fidelity + useful_expansion)
  core_intent      — one sentence: what the user actually wants to know
  legitimate_expansions — list: angles that ADD genuine value if library supports them
  off_limits_expansions — list: scope-creep angles that should be penalised

  # Legacy fields (still used by source_grounding, gap_accuracy, tension_surfacing)
  expected_sub_qs  — the sub-questions a good decomposition should produce (human-labeled)
  answerable_from_library  — list of sub-question indices (0-based) the library CAN answer
  unanswerable_sub_qs      — list of sub-question indices the library CANNOT answer
  key_article_titles       — titles of articles that must appear in the brief (grounding check)
  must_not_fabricate       — topics/claims that should NOT appear because library has nothing
  ideal_coverage           — "full" | "partial" | "thin"
  notes                    — why this case is interesting for the eval

Categories:
  MULTI_ANGLE     — question has inherently opposing or multiple distinct angles
  VOCAB_DIVERGE   — same topic stored under different vocabulary across articles
  PARTIAL_COVER   — library clearly addresses some sub-questions but not others
  SINGLE_ANGLE    — one tight sub-topic; decomposition should stay focused, not hallucinate
  TENSION         — library contains contradictory takes the brief must surface
  ENGAGEMENT_BIAS — high-engagement articles exist; brief should weight them

These map to the dimensions in the G-Eval rubric.
"""

from __future__ import annotations

CASES: list[dict] = [

    # -------------------------------------------------------------------------
    # MULTI_ANGLE — competing views that require both sides
    # -------------------------------------------------------------------------

    {
        "key": "ai_labor_displacement",
        "question": "What are the competing views on whether AI displaces or augments workers, and what does my library say about each?",
        "category": "MULTI_ANGLE",
        "core_intent": "Understand the displacement-vs-augmentation debate as represented in the user's library.",
        "legitimate_expansions": [
            "Worker/human experience of AI-driven workload change",
            "Economic or policy framing of automation timing",
        ],
        "off_limits_expansions": [
            "AI ethics or safety concerns not tied to labor",
            "Specific industries not mentioned in the articles",
            "Cross-national comparisons the library doesn't cover",
        ],
        "expected_sub_qs": [
            "What evidence does the library have for AI displacing jobs?",
            "What evidence does the library have for AI augmenting or creating jobs?",
            "What do economists specifically say about timing and policy responses?",
            "What is the worker/human perspective — how are people experiencing this?",
        ],
        "answerable_from_library": [0, 1, 3],
        "unanswerable_sub_qs": [2],
        "key_article_titles": [
            "What 81,000 people told us about the economics of AI",
            "Focus areas for The Anthropic Institute",
            "AI Doesn't Reduce Work—It Intensifies It",
            "Notes on AI, Labor, and China",
            "Anthropic Economic Index report",
        ],
        "must_not_fabricate": ["UBI policy specifics", "Congressional legislation on automation"],
        "ideal_coverage": "partial",
        "notes": "Library has displacement and augmentation angles but thin on policy responses. Good test for gap report accuracy.",
    },

    {
        "key": "llm_reliability_vs_utility",
        "question": "What does my library say about the reliability problems with LLMs, and how does that square with the case for using them?",
        "category": "TENSION",
        "core_intent": "Surface the tension between LLM reliability critiques and utility arguments as found in the user's library.",
        "legitimate_expansions": [
            "Specific failure patterns (hallucination, slot-machine behavior) that explain unreliability",
            "Contexts where reliability matters most vs. least",
        ],
        "off_limits_expansions": [
            "Technical architecture explanations of why LLMs hallucinate",
            "Benchmark comparisons across models",
            "Regulatory or governance angles not in the library",
        ],
        "expected_sub_qs": [
            "What are the reliability and failure mode arguments against LLMs?",
            "What is the case for LLM utility despite reliability concerns?",
            "What specific behaviors or patterns make LLMs unreliable?",
        ],
        "answerable_from_library": [0, 1, 2],
        "unanswerable_sub_qs": [],
        "key_article_titles": [
            "Pluralistic: LLMs are slot-machines",
            "The Year in Slop",
            "What Is Claude? Anthropic Doesn't Know, Either",
        ],
        "must_not_fabricate": ["specific benchmark numbers", "model weights or architecture details"],
        "ideal_coverage": "partial",
        "notes": "TENSION: slot-machine critique and AI slop coverage directly contradict the Claude/productivity utility case. Brief must name both sides and the contradiction between them.",
    },

    {
        "key": "glp1_evidence_vs_hype",
        "question": "Synthesize what my library says about GLP-1 drugs — what's the clinical evidence and what's the cultural commentary?",
        "category": "MULTI_ANGLE",
        "core_intent": "Understand what the user's library covers about GLP-1 drugs: clinical evidence and broader cultural response.",
        "legitimate_expansions": [
            "Addiction and behavioral effects beyond weight loss, if the library covers them",
            "Social or psychological dimensions of GLP-1 use",
        ],
        "off_limits_expansions": [
            "FDA approval timelines or regulatory history",
            "Clinical trial methodology or participant counts not in the articles",
            "Cultural backlash or social media commentary not in the library",
        ],
        "expected_sub_qs": [
            "What does the clinical evidence say about GLP-1 efficacy and mechanisms?",
            "What are the broader behavioral and addiction-related effects?",
            "What is the cultural or social reaction to these drugs?",
        ],
        "answerable_from_library": [0, 1],
        "unanswerable_sub_qs": [2],
        "key_article_titles": [
            "Can Ozempic Cure Addiction?",
        ],
        "must_not_fabricate": ["FDA approval dates", "clinical trial participant counts", "cultural backlash coverage"],
        "ideal_coverage": "partial",
        "notes": "Library has one deeply engaged article (5 highlights). Good test for engagement weighting — that article should dominate the brief.",
    },

    {
        "key": "ai_investment_landscape",
        "question": "What does my library say about the state of AI investment and how trustworthy the AI hype cycle is?",
        "category": "MULTI_ANGLE",
        "core_intent": "Understand how the user's library treats AI investment: skeptical coverage, hype critique, and any bullish case.",
        "legitimate_expansions": [
            "Specific investment risk patterns (shadow markets, valuation inflation) documented in the articles",
        ],
        "off_limits_expansions": [
            "Specific fund sizes or named investors not mentioned in the library",
            "Mainstream venture capital dynamics not covered",
            "Geopolitical dimensions of AI investment",
        ],
        "expected_sub_qs": [
            "What does the library say about AI investment patterns and risks?",
            "What criticisms of AI hype or inflated valuations are present?",
            "What is the mainstream case for AI investment?",
        ],
        "answerable_from_library": [0, 1],
        "unanswerable_sub_qs": [2],
        "key_article_titles": [
            "A Booming Shadow Market of Sketchy A.I. Investments",
            "Silicon Valley's Favorite Doomsaying Philosopher",
        ],
        "must_not_fabricate": ["specific fund sizes", "named investors or firms not in the library"],
        "ideal_coverage": "partial",
        "notes": "Tests whether the brief distinguishes skeptical coverage from booster coverage.",
    },

    {
        "key": "hustle_entrepreneurship_ethics",
        "question": "What does my library say about hustle culture and entrepreneurship — is the grind worth it, and what are the costs?",
        "category": "MULTI_ANGLE",
        "core_intent": "Surface the tension between pro-hustle and hustle-critique perspectives in the user's library, including the ethical and identity dimensions.",
        "legitimate_expansions": [
            "The relationship between ambition and identity or ethics, if the library covers it",
            "The emotional or psychological cost of hustle culture",
        ],
        "off_limits_expansions": [
            "Startup funding statistics or venture capital dynamics",
            "Named founders or companies not in the articles",
            "Academic research on burnout not referenced in the library",
        ],
        "expected_sub_qs": [
            "What is the case for hustle and entrepreneurial ambition?",
            "What are the critiques or costs of hustle culture?",
            "What does the library say about the relationship between ambition and ethics or identity?",
        ],
        "answerable_from_library": [0, 1, 2],
        "unanswerable_sub_qs": [],
        "key_article_titles": [
            "Management as AI superpower",
            "Reasonable Doubt at 30: Revisiting Jay-Z's Hustler Masterpiece in His Billionaire Era",
            "Why Is It So Hard to Be Ordinary?",
        ],
        "must_not_fabricate": ["startup funding statistics", "named founders or companies not in articles"],
        "ideal_coverage": "partial",
        "notes": "MULTI_ANGLE across very different registers: Jay-Z piece explores the hustler/gangster boundary and what ambition costs; 'Why Is It So Hard to Be Ordinary?' questions the cultural imperative to excel; 'Management as AI superpower' is unambiguously pro-hustle. Brief should surface the value tension, not flatten them into a unified 'here is what my library says about entrepreneurship'.",
    },

    # -------------------------------------------------------------------------
    # VOCAB_DIVERGE — same concept stored under different tags/titles
    # -------------------------------------------------------------------------

    {
        "key": "ai_cognitive_load",
        "question": "What have I read about AI's effect on how much mental work people have to do — does it reduce effort or create new kinds of burden?",
        "category": "VOCAB_DIVERGE",
        "core_intent": "Understand whether the user's library says AI reduces mental effort or introduces new cognitive burdens.",
        "legitimate_expansions": [
            "Attention and focus implications of AI tool use, if the library connects them to cognitive load",
            "Context engineering as a form of new cognitive work for managing AI agents",
        ],
        "off_limits_expansions": [
            "Cognitive psychology study results or attention span research not in the library",
            "AI in education or medical diagnosis — not what the user is asking about",
            "Hardware or brain-computer interface approaches to reducing cognitive load",
        ],
        "expected_sub_qs": [
            "Does AI reduce cognitive load or shift it?",
            "What kinds of new cognitive burdens does AI introduce?",
            "What does the library say about focus, attention, and AI tool use?",
        ],
        "answerable_from_library": [0, 1, 2],
        "unanswerable_sub_qs": [],
        "key_article_titles": [
            "AI Doesn't Reduce Work—It Intensifies It",
            "Pluralistic: LLMs are slot-machines",
            "Why Context Engineering? – Nextra",
            "Effective context engineering for AI agents",
        ],
        "must_not_fabricate": ["cognitive psychology study results", "attention span research numbers"],
        "ideal_coverage": "partial",
        "notes": "Vocabulary divergence: 'cognitive fatigue' tag vs 'context engineering' vs 'workload management'. Context engineering articles directly address managing attention scope in agent tool use — sub-Q 2 is answerable from them. Tests whether multi-query subagent bridges the vocabulary gap.",
    },

    {
        "key": "simplicity_software_design",
        "question": "What does my library say about the value of simplicity in software and digital products?",
        "category": "VOCAB_DIVERGE",
        "core_intent": "Understand what the user's library says about simplicity as a design value in software and digital products.",
        "legitimate_expansions": [
            "User trust and longevity as downstream effects of simplicity, if articles address it",
            "Over-featuredness and complexity as failure modes of products",
        ],
        "off_limits_expansions": [
            "Product revenue data or user survey statistics not in the articles",
            "Technical architecture simplicity (as opposed to product/UX simplicity)",
            "Minimalism as an aesthetic philosophy disconnected from software",
        ],
        "expected_sub_qs": [
            "What is the argument for simplicity in software tools?",
            "What happens when products become over-featured or complex?",
            "How does simplicity relate to user trust and longevity?",
        ],
        "answerable_from_library": [0, 1, 2],
        "unanswerable_sub_qs": [],
        "key_article_titles": [
            "TextEdit and the Relief of Simple Software",
            "Why I Finally Quit Spotify",
            "The Banality of Online Recommendation Culture",
        ],
        "must_not_fabricate": ["specific product revenue data", "user survey statistics"],
        "ideal_coverage": "partial",
        "notes": "Vocabulary divergence: 'simplicity in software' not a tag anywhere — stored as 'Spotify interface issues', 'graphical-user interface', 'digital organization'. Good retrieval breadth test.",
    },

    {
        "key": "digital_platform_user_control",
        "question": "Across what I've read, what are the arguments about how much control users have over digital platforms and recommendation systems?",
        "category": "VOCAB_DIVERGE",
        "core_intent": "Surface what the user's library says about user agency and control (or lack thereof) over digital platforms and algorithmic recommendation.",
        "legitimate_expansions": [
            "Specific platform examples (music, social media, AI) that illustrate the control problem",
            "Design patterns that remove or restore user agency",
        ],
        "off_limits_expansions": [
            "Platform engagement metrics or regulatory action details not in the library",
            "Antitrust or competition law angles",
            "Individual privacy or data rights — a related but distinct issue",
        ],
        "expected_sub_qs": [
            "How do recommendation algorithms reduce user agency?",
            "What design patterns specifically remove control from users?",
            "Are there examples of platforms that do this well or poorly?",
        ],
        "answerable_from_library": [0, 1, 2],
        "unanswerable_sub_qs": [],
        "key_article_titles": [
            "Why I Finally Quit Spotify",
            "The Banality of Online Recommendation Culture",
            "The Year in Slop",
            "AI Doesn't Reduce Work—It Intensifies It",
        ],
        "must_not_fabricate": ["platform engagement metrics", "regulatory action details"],
        "ideal_coverage": "partial",
        "notes": "Tests cross-article synthesis across different platform contexts (music, general web, AI).",
    },

    # -------------------------------------------------------------------------
    # PARTIAL_COVER — clear answer on some sub-questions, clear gap on others
    # -------------------------------------------------------------------------

    {
        "key": "forward_deployed_engineer",
        "question": "Based on what I've read, what is a forward-deployed engineer, what do they actually do, and is it a good career path?",
        "category": "PARTIAL_COVER",
        "core_intent": "Understand what the FDE role is, what skills it requires, and evaluate it as a career — using only what the user's library contains.",
        "legitimate_expansions": [
            "Career trajectory and compensation, if articles cover it",
            "How FDE differs from adjacent engineering roles",
        ],
        "off_limits_expansions": [
            "Specific company names or salary figures not mentioned in the articles",
            "FDE role at a specific company beyond what the articles say",
            "Critical perspectives the library doesn't actually contain — the gap should be flagged, not fabricated",
        ],
        "expected_sub_qs": [
            "What is the definition and role of a forward-deployed engineer?",
            "What skills and background does the role require?",
            "What is the career trajectory and compensation?",
            "What are the criticisms or downsides of the role?",
        ],
        "answerable_from_library": [0, 1, 2],
        "unanswerable_sub_qs": [3],
        "key_article_titles": [
            "Today's Hottest Role: Forward Deployed Engineer",
            "What are Forward Deployed Engineers, and why are they so in demand?",
            "The Forward Deployed AI Engineer: Architecting the Last Mile of AI",
            "What does it take to become a forward-deployed engineer?",
        ],
        "must_not_fabricate": ["specific company names not mentioned in articles", "salary figures not in the articles"],
        "ideal_coverage": "partial",
        "notes": "Library has substantial coverage on definition and skills, but no critical perspective. Gap report should flag this.",
    },

    {
        "key": "ai_alignment_safety",
        "question": "What does my library say about AI alignment and safety concerns — both the technical problems and who is working on them?",
        "category": "PARTIAL_COVER",
        "core_intent": "Understand what the user's library covers about AI alignment: technical problems, key actors, criticisms, and framing.",
        "legitimate_expansions": [
            "Criticisms of the AI safety community or its approach, if the library contains them",
            "Near-term vs. long-term framing of safety risks",
        ],
        "off_limits_expansions": [
            "Specific technical alignment proposals or paper titles not in the library",
            "Comprehensive history of the AI safety field",
            "Political or regulatory AI governance (distinct from technical safety)",
        ],
        "expected_sub_qs": [
            "What are the core technical AI safety/alignment problems?",
            "Who are the key organizations and researchers working on this?",
            "What are the criticisms of the AI safety community or its approach?",
            "What is the near-term vs. long-term framing of safety risks?",
        ],
        "answerable_from_library": [0, 1, 2],
        "unanswerable_sub_qs": [3],
        "key_article_titles": [
            "Focus areas for The Anthropic Institute",
            "Silicon Valley's Favorite Doomsaying Philosopher",
            "Automated Alignment Researchers",
            "Trustworthy agents in practice",
        ],
        "must_not_fabricate": ["specific technical alignment proposals", "paper titles not in library"],
        "ideal_coverage": "partial",
        "notes": "Library has organizational/institutional angle and critical angle, thin on technical specifics.",
    },

    {
        "key": "context_engineering_agents",
        "question": "Synthesize what I've read about context engineering for AI agents — what is it, why does it matter, and how should it be done?",
        "category": "PARTIAL_COVER",
        "core_intent": "Synthesize the user's library on context engineering: definition, importance, practical guidance, and failure modes.",
        "legitimate_expansions": [
            "Practical patterns for structuring agent context, if the library goes into implementation detail",
            "What goes wrong without good context engineering",
        ],
        "off_limits_expansions": [
            "Benchmark results or model comparisons not in the articles",
            "General LLM prompting techniques beyond context engineering",
            "Retrieval-augmented generation as a topic distinct from context engineering",
        ],
        "expected_sub_qs": [
            "What is context engineering and how is it defined?",
            "What are the key dimensions of context an agent needs?",
            "What does good context engineering look like in practice?",
            "What goes wrong without it?",
        ],
        "answerable_from_library": [0, 1, 2, 3],
        "unanswerable_sub_qs": [],
        "key_article_titles": [
            "Why Context Engineering? – Nextra",
            "Effective context engineering for AI agents",
            "Learn Claude Code",
            "Harness design for long-running application development",
            "Lecture 06. Initialize Before Every Agent Session",
        ],
        "must_not_fabricate": ["benchmark results", "specific model comparisons not in articles"],
        "ideal_coverage": "full",
        "notes": "Library has good coverage — this is a 'full' case that tests whether the brief is high quality when coverage is adequate.",
    },

    {
        "key": "mlops_system_design",
        "question": "What does my library say about designing scalable ML systems — tools, patterns, and tradeoffs?",
        "category": "PARTIAL_COVER",
        "core_intent": "Understand what the user's library covers about ML system design: tooling, patterns, and architectural tradeoffs.",
        "legitimate_expansions": [
            "Storage and queuing tradeoffs for ML data pipelines, if covered",
        ],
        "off_limits_expansions": [
            "Specific vendor pricing or benchmark throughput numbers not in articles",
            "ML theory or model training methodology",
            "Cloud provider comparisons not discussed",
        ],
        "expected_sub_qs": [
            "What are the key MLOps tools and what do they do?",
            "What system design patterns matter most for ML at scale?",
            "What are the tradeoffs between different data storage and queuing approaches?",
        ],
        "answerable_from_library": [0, 1, 2],
        "unanswerable_sub_qs": [],
        "key_article_titles": [
            "25 Top MLOps Tools You Need to Know in 2026",
            "System Design Key Technologies | Hello Interview",
            "System Design Interview Patterns | Hello Interview",
        ],
        "must_not_fabricate": ["specific vendor pricing", "benchmark throughput numbers"],
        "ideal_coverage": "partial",
        "notes": "MLOps tools and system design stored under very different vocabulary. Tests breadth of subagent retrieval. All three sub-questions are answerable: Hello Interview articles cover patterns and tradeoffs; 25 Top MLOps Tools covers tooling.",
    },

    # -------------------------------------------------------------------------
    # TENSION — library has contradictory takes; brief must surface them
    # -------------------------------------------------------------------------

    {
        "key": "ai_productivity_paradox",
        "question": "Does my library think AI makes people more productive, and what are the tensions in that claim?",
        "category": "TENSION",
        "core_intent": "Surface the tension between pro-productivity AI claims and counter-evidence in the user's library.",
        "legitimate_expansions": [
            "Quality vs. quantity dimension of AI output — are productivity gains real or illusory?",
        ],
        "off_limits_expansions": [
            "Productivity study statistics not in the articles",
            "AI productivity in manufacturing or physical labor",
            "General management or organizational productivity not tied to AI",
        ],
        "expected_sub_qs": [
            "What is the case for AI as a productivity multiplier?",
            "What evidence or arguments suggest AI reduces or distorts productivity?",
            "What does the library say about the quality vs. quantity dimension of AI output?",
        ],
        "answerable_from_library": [0, 1, 2],
        "unanswerable_sub_qs": [],
        "key_article_titles": [
            "Management as AI superpower",
            "AI Doesn't Reduce Work—It Intensifies It",
            "Pluralistic: LLMs are slot-machines",
            "The Year in Slop",
        ],
        "must_not_fabricate": ["productivity study statistics not in the articles"],
        "ideal_coverage": "full",
        "notes": "Classic TENSION case — 'Management as AI superpower' (pro-productivity) vs. 'AI Doesn't Reduce Work' and 'slot-machines'. Brief must name the contradiction.",
    },

    {
        "key": "tech_utopianism_vs_doomism",
        "question": "What does my library say about the relationship between Silicon Valley ideology and AI risk — is tech culture driving toward utopia, catastrophe, or something in between?",
        "category": "TENSION",
        "core_intent": "Map the competing ideological framings of AI's future — utopianism, doomism, institutional rationalism — as present in the user's library.",
        "legitimate_expansions": [
            "How these framings interact or contradict each other",
        ],
        "off_limits_expansions": [
            "Specific policy positions of named individuals not in the articles",
            "Quotes not in the articles",
            "Current events or news not referenced in the library",
        ],
        "expected_sub_qs": [
            "What is the ideological foundation of Silicon Valley's optimism about technology?",
            "What are the doomist or catastrophist arguments present in the library?",
            "How do these two framings interact or contradict each other?",
        ],
        "answerable_from_library": [0, 1, 2],
        "unanswerable_sub_qs": [],
        "key_article_titles": [
            "THE CALIFORNIAN IDEOLOGY",
            "Silicon Valley's Favorite Doomsaying Philosopher",
            "Focus areas for The Anthropic Institute",
        ],
        "must_not_fabricate": ["specific policy positions of named individuals", "quotes not in the articles"],
        "ideal_coverage": "partial",
        "notes": "TENSION: Californian Ideology piece critiques tech utopianism; doomsaying philosopher piece profiles catastrophism; Anthropic Institute piece represents institutional safety rationalism. These are three distinct and partially contradictory worldviews the brief must distinguish.",
    },

    {
        "key": "us_foreign_policy_iran",
        "question": "Based on what I've read, what is the state of US-Iran tensions and what are the key decision points?",
        "category": "TENSION",
        "core_intent": "Understand US-Iran strategic tensions and decision points as covered in the user's library.",
        "legitimate_expansions": [
            "Iranian domestic politics, if covered",
            "The case against US military intervention, if present",
        ],
        "off_limits_expansions": [
            "Specific troop numbers or classified assessments",
            "Names of Iranian officials not mentioned in the articles",
            "Broader regional context not covered in the library",
        ],
        "expected_sub_qs": [
            "What are the military and strategic considerations for US action on Iran?",
            "What are the arguments against US military intervention?",
            "What is the state of Iranian domestic politics?",
            "What is the broader regional context?",
        ],
        "answerable_from_library": [0, 1, 2],
        "unanswerable_sub_qs": [3],
        "key_article_titles": [
            "Analysis: The questions Trump must ask before striking Iran",
            "Opinion | Trump Is Banking on Iranian Weakness. That's a Mistake.",
        ],
        "must_not_fabricate": ["specific troop numbers", "classified assessments", "names of Iranian officials not mentioned"],
        "ideal_coverage": "partial",
        "notes": "High engagement on the CNN analysis (6 highlights). Tests engagement weighting — that article should anchor the brief.",
    },

    # -------------------------------------------------------------------------
    # SINGLE_ANGLE — focused question where decomposition should stay tight
    # -------------------------------------------------------------------------

    {
        "key": "star_interview_method",
        "question": "What does my library say about how to prepare for job interviews?",
        "category": "SINGLE_ANGLE",
        "core_intent": "Find out what the user's library says about job interview preparation — frameworks, methods, and practical steps.",
        "legitimate_expansions": [],
        "off_limits_expansions": [
            "AI-powered interview tools or AI-assisted preparation",
            "Technical interview preparation (coding challenges, system design)",
            "Behavioral psychology of interview stress or confidence",
            "Negotiation strategy after the interview",
            "Company research or job search strategy",
        ],
        "expected_sub_qs": [
            "What frameworks or methods does the library recommend for interview prep?",
            "What specific preparation steps are covered?",
        ],
        "answerable_from_library": [0, 1],
        "unanswerable_sub_qs": [],
        "key_article_titles": [
            "How to Prepare for an Interview | Grow with Google",
        ],
        "must_not_fabricate": ["interview tips not in the article", "company-specific advice"],
        "ideal_coverage": "thin",
        "notes": "SINGLE_ANGLE control case — should not hallucinate sub-questions. One highly engaged article (5 highlights) should dominate.",
    },

    {
        "key": "alzheimers_biomarkers",
        "question": "What does my library say about early detection of Alzheimer's disease?",
        "category": "SINGLE_ANGLE",
        "core_intent": "Find what the user's library covers about early Alzheimer's detection — biomarker approaches and clinical promise.",
        "legitimate_expansions": [],
        "off_limits_expansions": [
            "Other biomarker studies not in the library",
            "FDA approval status or regulatory timelines",
            "Specific lab names or research institutions not in the article",
            "Treatment options for Alzheimer's — distinct from detection",
            "General aging or dementia content beyond Alzheimer's detection",
        ],
        "expected_sub_qs": [
            "What biomarker approaches to early detection are covered?",
            "What is the clinical promise of early detection methods?",
        ],
        "answerable_from_library": [0, 1],
        "unanswerable_sub_qs": [],
        "key_article_titles": [
            "Blood test holds promise for predicting when Alzheimer's symptoms will start",
        ],
        "must_not_fabricate": ["other biomarker studies", "FDA approval status", "specific lab names"],
        "ideal_coverage": "thin",
        "notes": "Low engagement (0 highlights). Tests whether brief correctly flags thin, unengaged coverage vs inflating it.",
    },

    # -------------------------------------------------------------------------
    # ENGAGEMENT_BIAS — tests that high-highlight articles dominate the brief
    # -------------------------------------------------------------------------

    {
        "key": "ai_engineer_career",
        "question": "What does my library say about the AI engineering job market and what skills matter?",
        "category": "ENGAGEMENT_BIAS",
        "core_intent": "Understand what the user's library says about the AI engineering job market: demand, skills, roles, and differentiation from adjacent roles.",
        "legitimate_expansions": [
            "How AI engineering differs from data science or traditional ML engineering",
            "Specializations within AI engineering (FDE, applied ML, infra)",
        ],
        "off_limits_expansions": [
            "Specific salary numbers or company hiring statistics not in the articles",
            "AI engineering outside the job market frame (research, open source)",
            "Advice on how to break into AI engineering beyond what the articles say",
        ],
        "expected_sub_qs": [
            "What is the current state of AI engineering demand?",
            "What skills and backgrounds are employers looking for?",
            "What roles and specializations exist in AI engineering?",
            "How does AI engineering differ from adjacent roles like data science?",
        ],
        "answerable_from_library": [0, 1, 2, 3],
        "unanswerable_sub_qs": [],
        "key_article_titles": [
            "AI Engineer Job Outlook 2025: Trends, Salaries, and Skills",
            "Today's Hottest Role: Forward Deployed Engineer",
            "What are Forward Deployed Engineers, and why are they so in demand?",
            "The Forward Deployed AI Engineer",
        ],
        "must_not_fabricate": ["specific salary numbers not in articles", "company hiring statistics"],
        "ideal_coverage": "partial",
        "notes": "AI Engineer article has 2 highlights; FDE articles have 1. Tests whether engagement signal correctly weights the more-engaged articles.",
    },

    {
        "key": "music_streaming_culture",
        "question": "What does my library say about how music streaming platforms have changed how we listen to and value music?",
        "category": "ENGAGEMENT_BIAS",
        "core_intent": "Understand what the user's library says about streaming's effect on music consumption behavior, and what has been lost in the shift.",
        "legitimate_expansions": [
            "The psychology or meaning of music listening, if a library article addresses it",
        ],
        "off_limits_expansions": [
            "Spotify user numbers or streaming revenue statistics",
            "Bad Bunny or Super Bowl content — about cultural identity, not streaming platforms",
            "Music industry economics or artist compensation",
        ],
        "expected_sub_qs": [
            "How have streaming interfaces changed music consumption behavior?",
            "What has been lost in the shift from ownership to streaming?",
            "What does the library say about the psychology or meaning of music listening?",
        ],
        "answerable_from_library": [0, 1, 2],
        "unanswerable_sub_qs": [],
        "key_article_titles": [
            "Why I Finally Quit Spotify",
            "What a Rare Condition Can Teach Us About the Power of Music",
        ],
        "must_not_fabricate": ["Spotify user numbers", "streaming revenue statistics", "Bad Bunny or Super Bowl content"],
        "ideal_coverage": "partial",
        "notes": "Why I Finally Quit Spotify has 3 highlights and anchors the interface/behavior angle. Music psychology article addresses what listening means at a deeper level. Bad Bunny is explicitly in must_not_fabricate — it's about cultural identity, not streaming platforms.",
    },

    {
        "key": "capitalism_wellness_mindfulness",
        "question": "What have I read about the relationship between capitalism and wellness culture?",
        "category": "ENGAGEMENT_BIAS",
        "core_intent": "Find what the user's library says about how capitalism has co-opted wellness and mindfulness, and what the critiques are.",
        "legitimate_expansions": [],
        "off_limits_expansions": [
            "Academic studies on mindfulness not in the library",
            "Corporate wellness program statistics",
            "Red-light therapy or biohacking content as capitalism critique — not the same topic",
            "Self-help industry critique beyond what the articles cover",
        ],
        "expected_sub_qs": [
            "How has capitalism co-opted wellness and mindfulness practices?",
            "What are the critiques of corporate or commodified wellness?",
            "What specific mechanisms or examples does the library cover?",
        ],
        "answerable_from_library": [0, 1, 2],
        "unanswerable_sub_qs": [],
        "key_article_titles": [
            "How Capitalism Turned Mindfulness Into a Productivity Hack",
        ],
        "must_not_fabricate": [
            "academic studies on mindfulness",
            "corporate wellness program statistics",
            "red-light therapy or biohacking content as capitalism critique",
        ],
        "ideal_coverage": "thin",
        "notes": "Only the mindfulness article (1 highlight) is directly on-topic. Red-Light Therapy piece is consumer personal essay — tangentially related but not a capitalism critique. Brief should stay narrow and not pad with the off-topic article. Tests whether agent correctly scopes thin coverage.",
    },

    {
        "key": "us_trade_policy_tariffs",
        "question": "What does my library say about US trade policy and tariffs under the current administration?",
        "category": "PARTIAL_COVER",
        "core_intent": "Understand what the user's library covers about US tariff actions, economic arguments, and international consequences.",
        "legitimate_expansions": [],
        "off_limits_expansions": [
            "Specific tariff rates not in the article",
            "Country-by-country trade data",
            "GDP impact estimates",
            "Economic theory of tariffs beyond what the article contains",
        ],
        "expected_sub_qs": [
            "What tariff actions has the administration taken and under what authority?",
            "What are the economic arguments for and against the tariff approach?",
            "What is the international reaction and geopolitical consequence?",
        ],
        "answerable_from_library": [0],
        "unanswerable_sub_qs": [1, 2],
        "key_article_titles": [
            "After Supreme Court Loss, Trump Plans to Impose Global Tariffs Using Different Laws",
        ],
        "must_not_fabricate": ["specific tariff rates", "country-by-country trade data", "GDP impact estimates"],
        "ideal_coverage": "thin",
        "notes": "Very thin library coverage (one unengaged article). Gap report should be the main output. Tests that brief doesn't over-synthesize from a single thin source.",
    },
]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate():
    keys = [c["key"] for c in CASES]
    assert len(keys) == len(set(keys)), "Duplicate case keys"
    assert len(CASES) == 21, f"Expected 21 cases, got {len(CASES)}"
    for c in CASES:
        assert c["category"] in {
            "MULTI_ANGLE", "VOCAB_DIVERGE", "PARTIAL_COVER",
            "SINGLE_ANGLE", "TENSION", "ENGAGEMENT_BIAS",
        }, f"Unknown category: {c['category']}"
        assert c["ideal_coverage"] in {"full", "partial", "thin"}
        assert "core_intent" in c, f"{c['key']}: missing core_intent"
        assert "legitimate_expansions" in c, f"{c['key']}: missing legitimate_expansions"
        assert "off_limits_expansions" in c, f"{c['key']}: missing off_limits_expansions"
        for i in c["answerable_from_library"]:
            assert i < len(c["expected_sub_qs"]), f"{c['key']}: answerable index {i} out of range"
        for i in c["unanswerable_sub_qs"]:
            assert i < len(c["expected_sub_qs"]), f"{c['key']}: unanswerable index {i} out of range"


_validate()


# ---------------------------------------------------------------------------
# Category counts (for documentation)
# ---------------------------------------------------------------------------

from collections import Counter  # noqa: E402

CATEGORY_COUNTS = Counter(c["category"] for c in CASES)
# MULTI_ANGLE: 4, VOCAB_DIVERGE: 3, PARTIAL_COVER: 5,
# SINGLE_ANGLE: 2, TENSION: 4, ENGAGEMENT_BIAS: 3
