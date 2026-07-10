"""
G-Eval rubric for memory consolidation prompt quality.

Five orthogonal dimensions. Each scored 1–5 by an LLM judge following
explicit chain-of-thought steps. Weighted sum → final score 0.0–1.0.
Pass threshold: weighted score ≥ 0.70.

Dimensions:
  1. specificity        (0.25) — is current_focus a sub-domain, not a category?
  2. trajectory         (0.30) — does memory_text describe what the user is working toward?
  3. depth_asymmetry    (0.20) — does it distinguish deep vs. shallow engagement by domain?
  4. behavioral_pattern (0.15) — does it name save/read/highlight behavior, not just topics?
  5. faithfulness       (0.10) — are all claims grounded in the activity shown? no invented facts?

Weights sum to 1.0. Faithfulness is a guard rail: any score < 2 on faithfulness
fails the case regardless of weighted total (a hallucinating profile is unusable).
"""

from __future__ import annotations

WEIGHTS = {
    "specificity": 0.25,
    "trajectory": 0.30,
    "depth_asymmetry": 0.20,
    "behavioral_pattern": 0.15,
    "faithfulness": 0.10,
}

PASS_THRESHOLD = 0.70
FAITHFULNESS_FLOOR = 2  # raw 1–5 score; below this = hard fail

# ---------------------------------------------------------------------------
# Anchors (1 = bad, 3 = acceptable, 5 = ideal)
# These are shown to the judge verbatim.
# ---------------------------------------------------------------------------

ANCHORS = {
    "specificity": {
        1: "current_focus names only a broad parent field (e.g., 'artificial intelligence', "
           "'technology', 'economics'). No sub-domain, research angle, or application area named.",
        3: "current_focus names a recognizable sub-field (e.g., 'machine learning', 'NLP') "
           "but not a specific application, role, or research angle. Somewhat useful but generic.",
        5: "current_focus names a precise sub-domain with context (e.g., 'AI engineering for "
           "production agent systems', 'context engineering and LLM eval infrastructure', "
           "'forward-deployed AI roles at early-stage companies'). A future assistant reading "
           "this would know exactly what to prioritize.",
    },
    "trajectory": {
        1: "memory_text describes only what topics appear in the library. No sense of where "
           "the user is going, what they are building toward, or why they are reading this. "
           "Could describe any reader of these topics.",
        3: "memory_text mentions a goal or direction but vaguely ('user seems to be exploring "
           "AI'). It is plausible but could apply to millions of people. Does not distinguish "
           "this user's specific situation or intent.",
        5: "memory_text states a specific trajectory grounded in observed signals: what the "
           "user appears to be preparing for, building, or deciding — with the behavioral "
           "evidence for that conclusion (e.g., 'reading list titled X combined with deep "
           "reads on Y suggests preparing for Z'). A future assistant could act on this.",
    },
    "depth_asymmetry": {
        1: "A single reading_velocity label is given with no further elaboration. The profile "
           "treats all reading behavior as uniform. Does not distinguish which topics the user "
           "engages deeply vs. skims.",
        3: "memory_text acknowledges that engagement varies but does not specify which domains "
           "or content types. e.g., 'user sometimes highlights heavily' without saying on what.",
        5: "memory_text explicitly names at least one domain/content-type where the user goes "
           "deep (high read%, multiple highlights) AND at least one where they skim (low read%, "
           "few highlights), with the behavioral evidence for each. reading_velocity reflects "
           "the dominant pattern, not an average.",
    },
    "behavioral_pattern": {
        1: "Profile describes only topic interests. No mention of save/read ratio, highlight "
           "behavior, list creation, backlog accumulation, or any other behavioral signal. "
           "Indistinguishable from a topic tag cloud.",
        3: "Profile mentions one behavioral signal (e.g., 'user highlights frequently' or "
           "'user has a large backlog') without integrating it into the trajectory or depth "
           "picture. The observation is present but isolated.",
        5: "Profile integrates behavioral signals into a coherent picture: e.g., 'saves "
           "heavily on [topic] but rarely opens those articles — possible anxiety-saving or "
           "unresolved ambivalence'; or 'annotates densely on technical content, suggesting "
           "active synthesis rather than passive reading'. The pattern tells you something "
           "about how to serve this user differently.",
    },
    "faithfulness": {
        1: "Profile makes specific claims that are not supported by any element of the "
           "activity string — invented article titles, fabricated read percentages, claimed "
           "highlights that do not appear, or inferred facts with no textual basis.",
        3: "Profile stays close to the activity but makes one or two reasonable inferences "
           "that slightly overreach the evidence (e.g., inferring a career goal from a "
           "reading list title without other corroboration). No outright fabrication.",
        5: "Every specific claim in the profile is directly traceable to something in the "
           "activity string: titles match, read% values are cited or used accurately, "
           "highlight quotes are from the activity, list names are used as stated. "
           "Inferences are clearly framed as inferences ('suggests', 'appears to').",
    },
}

# ---------------------------------------------------------------------------
# Judge prompt
# ---------------------------------------------------------------------------

JUDGE_SYSTEM = """\
You are an evaluation judge for a personal reading assistant.
You will be given:
  1. The activity data that was shown to the AI (what it had to work with)
  2. The profile the AI produced
  3. A scoring dimension with anchors at 1 (bad), 3 (acceptable), 5 (ideal)

Your job: score the profile on this dimension by following these steps exactly.

Chain-of-thought steps:
1. Read the anchor descriptions for scores 1, 3, and 5.
2. Find specific evidence in the profile that is relevant to this dimension.
3. Find specific evidence in the activity that confirms or contradicts the profile's claims.
4. Decide: does the profile achieve the 5-anchor, the 3-anchor, or the 1-anchor?
   Use 2 or 4 for cases between anchors.
5. State your score and a one-sentence reason citing specific text.

Respond with JSON only:
{"score": <integer 1-5>, "reason": "<one sentence citing specific text>"}
"""

JUDGE_USER = """\
Activity shown to the AI:
{activity}

Profile produced:
current_focus: {current_focus}
reading_velocity: {reading_velocity}
memory_text: {memory_text}

Dimension: {dimension}

Anchor 1 (bad):
{anchor_1}

Anchor 3 (acceptable):
{anchor_3}

Anchor 5 (ideal):
{anchor_5}
"""


def build_judge_prompt(
    activity: str,
    current_focus: str,
    reading_velocity: str,
    memory_text: str,
    dimension: str,
) -> tuple[str, str]:
    """Return (system_prompt, user_prompt) for a single dimension score."""
    anchors = ANCHORS[dimension]
    user = JUDGE_USER.format(
        activity=activity,
        current_focus=current_focus,
        reading_velocity=reading_velocity,
        memory_text=memory_text,
        dimension=dimension,
        anchor_1=anchors[1],
        anchor_3=anchors[3],
        anchor_5=anchors[5],
    )
    return JUDGE_SYSTEM, user


def weighted_score(dimension_scores: dict[str, int]) -> float:
    """Convert raw 1–5 dimension scores to weighted 0.0–1.0 total."""
    total = 0.0
    for dim, weight in WEIGHTS.items():
        raw = dimension_scores.get(dim, 1)
        normalized = (raw - 1) / 4.0  # 1→0.0, 5→1.0
        total += normalized * weight
    return round(total, 4)


def is_hard_fail(dimension_scores: dict[str, int]) -> bool:
    """Faithfulness below floor = unusable profile regardless of other scores."""
    return dimension_scores.get("faithfulness", 1) < FAITHFULNESS_FLOOR
