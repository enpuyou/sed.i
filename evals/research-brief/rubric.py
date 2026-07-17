"""
G-Eval rubric for the Library Research Brief (v2).

Six dimensions, weights summing to 1.0.
Each dimension has chain-of-thought steps and anchors at 1 (bad), 3 (acceptable), 5 (ideal).
Pass threshold: weighted score >= 0.70.

Dimension weights:
  question_fidelity       0.25  — does the brief answer what the user actually asked?
  useful_expansion        0.10  — are any expansions beyond the core question genuinely additive?
  source_grounding        0.25  — are claims tied to real retrieved articles?
  gap_accuracy            0.20  — does the gap report correctly identify unanswerable sub-Qs?
  synthesis_quality       0.10  — does the brief synthesize, not just list?
  tension_surfacing       0.10  — are contradictions in the library named explicitly?

Rationale for weights (v2):
  question_fidelity replaces sub_question_coverage. The old dimension penalised any brief
  that didn't match a fixed expected_sub_qs list. The new dimension asks: does the brief
  serve what the user actually wants to know (core_intent)? A brief can use different
  sub-questions than the expected list and still score 5 if it satisfies the core intent.
  useful_expansion is new: it rewards briefs that expand scope in genuinely helpful ways
  (within legitimate_expansions) and penalises scope creep (off_limits_expansions).
  Together question_fidelity + useful_expansion replace sub_question_coverage at 0.35 → 0.35.
  synthesis_quality weight reduced from 0.15 → 0.10 to make room.
"""

from __future__ import annotations

RUBRIC_DIMENSIONS = {
    "question_fidelity": {
        "weight": 0.25,
        "description": (
            "Does the brief answer what the user actually asked? Use core_intent from the case "
            "as the reference point — not the expected_sub_qs list. The brief may use different "
            "sub-questions than the expected list and still score 5 if it satisfies core_intent. "
            "Penalise briefs that ignore the core intent or pivot to a different topic."
        ),
        "cot_steps": [
            "Read core_intent from the case. State in one sentence what the user actually wanted.",
            "Read the brief's summary and sub-question findings.",
            "Ask: does the brief's output directly address core_intent? What fraction of the findings are directly relevant?",
            "Check for topic drift: does the brief answer a question the user didn't ask?",
            "For SINGLE_ANGLE cases with thin ideal_coverage: the brief should stay narrow. Expanding beyond the one article's content is a fidelity failure.",
            "Score based on how fully and directly core_intent is served.",
        ],
        "anchors": {
            1: "Brief does not address core_intent — it answers a different question or generates sub-questions unrelated to what the user asked.",
            3: "Brief partially addresses core_intent but drifts into adjacent topics, or misses one key dimension of what the user asked.",
            5: "Brief directly and fully addresses core_intent. Every finding is clearly relevant to what the user asked.",
        },
    },

    "useful_expansion": {
        "weight": 0.10,
        "description": (
            "If the brief expands beyond the minimum needed to answer the user's question, is that "
            "expansion genuinely useful? Use legitimate_expansions and off_limits_expansions from "
            "the case. A brief that stays exactly at the minimum still scores 3 (neutral). "
            "Expansion into off_limits_expansions should score 1. Expansion into legitimate_expansions "
            "that the library actually supports scores 5."
        ),
        "cot_steps": [
            "List any sub-questions or findings the brief addresses beyond the core_intent minimum.",
            "Check: do any of these appear in legitimate_expansions from the case?",
            "Check: do any of these appear in off_limits_expansions from the case?",
            "If no expansion beyond core_intent: score 3 (neutral — not a failure, not a bonus).",
            "If expansion matches legitimate_expansions AND is grounded in the library: score higher.",
            "If expansion matches off_limits_expansions OR is not grounded in any library article: score lower.",
        ],
        "anchors": {
            1: "Brief adds sub-questions from off_limits_expansions, or invents angles not in the library and not in legitimate_expansions.",
            3: "Brief addresses only core_intent with no expansion (neutral), or expansion is present but unclear whether it falls in legitimate or off-limits.",
            5: "Brief expands into legitimate_expansions with grounded findings, making the output genuinely more valuable than a minimum answer.",
        },
    },

    "source_grounding": {
        "weight": 0.25,
        "description": (
            "Are the brief's claims and findings tied to specific articles from the library? "
            "Judge whether key_article_titles from the case appear as sources, and whether "
            "claims are traceable to articles rather than generated from the LLM's parametric knowledge."
        ),
        "cot_steps": [
            "List all article titles cited in the brief.",
            "Check: do the key_article_titles from the case appear?",
            "Check: does the brief cite articles not in the must_not_fabricate list with fabricated details?",
            "For each major claim in the brief, ask: is there a cited article that plausibly supports it?",
            "Flag any claim that reads as general knowledge (not from a specific article) — these are grounding failures.",
        ],
        "anchors": {
            1: "Brief makes claims without citing specific articles, or cites articles with details not in those articles. Key articles absent.",
            3: "Most claims are grounded. Key articles are present but some supporting details are vague or potentially fabricated.",
            5: "All major claims are tied to specific cited articles. Key articles are present. No details from must_not_fabricate appear.",
        },
    },

    "gap_accuracy": {
        "weight": 0.20,
        "description": (
            "Does the gap report honestly reflect what the library cannot answer? "
            "Judge by TOPIC, not by whether the brief used the exact expected sub-questions. "
            "You are given: (a) topics the library provably cannot answer — a gap on one of these is correct; "
            "(b) topics the library provably can answer — a gap on one of these is a false gap. "
            "The brief may use different wording than the case labels; judge the substance."
        ),
        "cot_steps": [
            "FIRST: check library_cant_answer. If it is empty AND `gaps` is empty: STOP — score 5 immediately. No gaps were expected and none were reported. Do not read further.",
            "FIRST: check library_cant_answer. If it is empty AND `gaps` is non-empty: STOP — score 1. The brief reported gaps when none were possible.",
            "Look ONLY at the brief's `gaps` array. List every entry. Do NOT treat `coverage_assessment: none` in sub_question_findings as a gap — only entries in `gaps` count.",
            "For each entry in `gaps`: does it correspond to a topic in library_cant_answer (topics the library provably CANNOT answer)? If yes, it is a true gap. library_can_answer lists topics the library CAN answer — entries there are NOT gaps.",
            "Are there topics in library_cant_answer that are missing from `gaps`? Each omission is a miss — penalise.",
            "If ideal_coverage is 'thin' and library_cant_answer is empty: no sub-question gaps to flag. Score 3 if the brief is honest about thin coverage, 5 only if it describes what kind of source would enrich it.",
            "Are the gap descriptions actionable? Do they name what kind of source, data, or angle would fill the gap?",
        ],
        "anchors": {
            1: "library_cant_answer is non-empty but `gaps` is empty (missed real gaps), OR `gaps` contains entries for topics in library_can_answer (false gaps), OR `gaps` is non-empty when library_cant_answer is empty (fabricated gaps).",
            3: "`gaps` is partially correct — identifies some real gaps from library_cant_answer but misses one, or includes one false gap. Descriptions are vague.",
            5: "`gaps` correctly reports every topic in library_cant_answer, no false entries, descriptions are actionable. Empty `gaps` with empty library_cant_answer also scores 5.",
        },
    },

    "synthesis_quality": {
        "weight": 0.10,
        "description": (
            "Does the brief synthesize across sources, or does it just summarize each article "
            "in turn? A good brief draws connections, states what the library collectively says "
            "on a sub-question, and produces a finding that could not be obtained by reading "
            "article summaries in sequence."
        ),
        "cot_steps": [
            "Read the findings section. Does each finding make a claim about what 'the library says', or does it just describe what 'Article X says, Article Y says'?",
            "Check for cross-article synthesis: does the brief draw connections between articles on the same sub-question?",
            "Check the summary: is it a distillation of the sub-question findings, or just a repeat of the question?",
            "Would you learn something from this brief that you could not get by reading each article description separately?",
        ],
        "anchors": {
            1: "Brief is a list of article descriptions with no synthesis. Each article gets its own paragraph with no connections drawn.",
            3: "Brief attempts synthesis but mostly paraphrases articles in turn. A few cross-article connections are made.",
            5: "Brief synthesizes across sources. Findings state what the library collectively says. Cross-article connections are explicit and substantive.",
        },
    },

    "tension_surfacing": {
        "weight": 0.10,
        "description": (
            "Does the brief name contradictions or tensions within the library where they exist? "
            "For MULTI_ANGLE and TENSION cases this is critical. For SINGLE_ANGLE cases, "
            "score 3 (neutral) if there are no tensions to surface."
        ),
        "cot_steps": [
            "Check the case category. If SINGLE_ANGLE and ideal_coverage is 'thin', score 3 by default unless tensions are fabricated.",
            "For MULTI_ANGLE and TENSION cases: does the brief identify that the library contains competing views?",
            "Are the specific articles on each side named?",
            "Does the brief note cross-cutting tensions (contradictions that span sub-questions)?",
            "Penalize if tensions are implied but never stated explicitly.",
        ],
        "anchors": {
            1: "MULTI_ANGLE/TENSION case: no tensions mentioned despite library containing clear contradictions.",
            3: "Tensions acknowledged vaguely ('the library has mixed views') but not named with specific articles.",
            5: "Tensions named explicitly with the specific articles or claims on each side. Cross-cutting tensions noted where present.",
        },
    },
}

WEIGHTS = {k: v["weight"] for k, v in RUBRIC_DIMENSIONS.items()}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, f"Weights must sum to 1.0, got {sum(WEIGHTS.values())}"

PASS_THRESHOLD = 0.70
HARD_FAIL_CONDITIONS = [
    # Any of these make the run fail regardless of weighted score
    "fabricated_citation",      # cited an article not in retrieved set
    "no_gap_report",            # PARTIAL_COVER / TENSION case with unanswerable sub-Qs but no gap section
    "zero_source_grounding",    # no article titles cited at all in a library with relevant content
]

SYSTEM_PROMPT = """\
You are evaluating the output of a Library Research Brief agent. The agent was given
a research question and a user's personal reading library, and produced a structured
brief synthesizing what the library says.

You will score one dimension at a time. For each dimension you will:
1. Follow the chain-of-thought steps provided.
2. State your reasoning concisely (2-4 sentences).
3. Assign a score from 1 to 5 using the anchors provided.

Be calibrated: a score of 3 means "acceptable but not ideal." Do not inflate scores.
A brief that correctly identifies gaps in a thin library should score high on
gap_accuracy even if the synthesis is short.
"""
