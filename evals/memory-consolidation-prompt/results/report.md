# Eval Report: memory-consolidation-prompt
Date: 2026-07-10
Dataset: pilot (10 cases)
Status: **SHIP — Variant B**

---

## Table of Contents

1. [Metric Definitions](#1-metric-definitions)
2. [Variants](#2-variants)
3. [Prompt History](#3-prompt-history)
4. [Dataset Taxonomy](#4-dataset-taxonomy)
5. [Aggregate Results](#5-aggregate-results)
6. [Per-Dimension Breakdown](#6-per-dimension-breakdown)
7. [Per-Case Table](#7-per-case-table)
8. [Wins and Regressions vs A](#8-wins-and-regressions-vs-a)
9. [Residual Failure Taxonomy](#9-residual-failure-taxonomy)
10. [Phase 9 Decision](#10-phase-9-decision)

---

## 1. Metric Definitions

### Weighted rubric score (0.0–1.0)
Five G-Eval dimensions scored 1–5 by an LLM judge, normalized to 0–1 per dimension, then combined:

| Dimension | Weight | What it measures |
|-----------|--------|-----------------|
| specificity | 0.25 | Is `current_focus` a named sub-domain, not a parent category? |
| trajectory | 0.30 | Does `memory_text` describe what the user is building toward or preparing for? |
| depth_asymmetry | 0.20 | Does the profile distinguish deep vs. shallow engagement by domain? |
| behavioral_pattern | 0.15 | Does the profile name save/read/highlight behaviors, not just topics? |
| faithfulness | 0.10 | Are all claims grounded in the activity shown? No invented facts? |

**Score 0.0**: a profile that is a topic tag cloud with no behavioral or trajectory signal.
**Score 0.70**: pass threshold — minimum for a profile to be considered useful.
**Score 1.0**: all five dimensions at anchor 5; profile would genuinely help a future assistant serve this user differently.

**1pp delta on 10 cases = 0.1 cases changing.** Treat deltas < 0.05 as directional only; deltas ≥ 0.10 are meaningful at this scale.

### Pass rate
Fraction of cases with weighted score ≥ 0.70 AND no faithfulness hard fail.

### Hard fail
Any case where the `faithfulness` raw score < 2 — profile contains invented facts or claims not traceable to the activity. Automatically fails regardless of weighted total.

---

## 2. Variants

**A — Baseline (old single prompt)**
- Single undifferentiated prompt. No explicit instructions about trajectory, depth asymmetry, or backlog patterns.
- Output schema: `current_focus`, `reading_velocity`, `knowledge_gaps` (list), `episodic_events` (list).
- Fixed 7-day lookback window (not represented in this eval — activity strings are pre-formatted).
- No inline highlight text, no save/read signal separation in the format.
- Prompt instruction: *"Extract: current_focus, reading_velocity, knowledge_gaps, episodic_events. Be concise and accurate."*

**B — New prompt (prescriptive four-dimension checklist)**
- Split bootstrap/delta architecture (bootstrap used for all pilot cases).
- Output schema: `current_focus`, `reading_velocity`, `memory_text` (free prose).
- Activity string includes inline highlights per article, "saved but never opened" section, reading list names, timestamps, and chronological read order.
- Prompt explicitly instructs coverage of four dimensions: trajectory, depth asymmetry, behavioral pattern, unread backlog signal.
- Explicit specificity rule: *"Be specific about sub-domain, not just the parent field. BAD: 'artificial intelligence'. GOOD: 'AI engineering and agent systems — context engineering, LLM evals, forward-deployed roles'."*

**C — Alternative (briefing framing)**
- Same output schema as B. Same enriched activity string format as B.
- Prompt framing: *"Write a briefing note from one assistant to another. Include only what would genuinely help a future assistant give better responses to this user."*
- No dimension checklist. Tests whether emergent prioritization from the briefing frame matches or exceeds the explicit checklist.

---

## 3. Prompt History

The consolidation prompt has gone through two meaningful generations in this branch:

**Generation 1 (variant A baseline):**
- Simple instruction to extract structured fields.
- `knowledge_gaps` and `episodic_events` as output fields caused problems: the model attempted to fill UUIDs in `content_item_id` with article titles, crashing on UUID parsing. These fields were removed.
- Bootstrap window was hardcoded at 7 days, truncating data for users who installed weeks prior.
- Activity string contained no highlights, no save/read separation, no timestamps.

**Generation 2 (variants B and C, current branch):**
- Redesigned to hybrid schema: typed fields (`current_focus`, `reading_velocity`) for programmatic use + `memory_text` (free prose) for everything else.
- Bootstrap detection changed to dynamic: finds earliest actual activity up to 30 days back.
- Activity string enriched: inline highlights per article (up to 3), "Read deeply / without highlights / Saved but never opened" sections, reading list names, relative timestamps, chronological read order.
- Two prompt philosophies tested: explicit dimension checklist (B) vs. briefing framing (C).

---

## 4. Dataset Taxonomy

10 synthetic pilot cases, each with a fabricated activity string and human-written ideal labels. Cases were labeled before any variant was run.

| Key | Primary pattern | What it exercises |
|-----|----------------|-------------------|
| `heavy_saver_no_reads` | Save burst, no reads | Burst-saving detection, backlog behavioral pattern |
| `deep_reader_narrow` | Deep single-thread reads in sequence | Specificity, sequencing signal, consistent depth |
| `topic_shift` | Economics early → AI engineering later | Temporal pivot detection, depth asymmetry |
| `intent_reading_list` | 48h job-search sprint + named lists | Trajectory from explicit intent signals |
| `technical_deep_news_skim` | Deep technical + news skim same days | Canonical depth_asymmetry case |
| `backlog_accumulator` | 90-minute philosophy save burst, 0 opens | Single-session burst, 5-day no-follow-through |
| `re_reader` | Re-reading canon papers, orphan highlights | Re-engagement / synthesis behavioral pattern |
| `eclectic_browser` | 10 unrelated articles across 7 days | Hard case: resist false coherence, browsing mode |
| `synthesizer` | Low read% + dense highlights + 3 lists | Annotation-while-skimming behavioral pattern |
| `sparse_new_user` | 2 articles in 1 hour | Faithfulness guard rail: no hallucination under thin data |

**Case labels** are human-authored (`ideal` dict per case in `dataset/pilot.py`). Not yet formally approved — treat scores as directional until user sign-off on labels.

---

## 5. Aggregate Results

| Variant | Mean weighted score | Pass rate | Hard fail rate | N |
|---------|--------------------|-----------|-----------------|----|
| A (baseline) | 0.621 | 3/10 (30%) | 0/10 | 10 |
| **B (prescriptive dims)** | **0.860** | **9/10 (90%)** | **0/10** | **10** |
| C (briefing framing) | 0.710 | 6/10 (60%) | 0/10 | 10 |

**B outperforms A by +0.239.** B outperforms C by +0.150. No hard fails in any variant — no profile hallucinated facts in a way the judge scored < 2 on faithfulness.

---

## 6. Per-Dimension Breakdown

Mean raw score (1–5) per dimension:

| Dimension | A mean | B mean | C mean | B−A | C−A |
|-----------|--------|--------|--------|-----|-----|
| specificity (0.25) | 4.0 | 4.6 | 4.5 | +0.6 | +0.5 |
| **trajectory (0.30)** | **2.8** | **4.1** | **3.1** | **+1.3** | **+0.3** |
| **depth_asymmetry (0.20)** | **3.4** | **4.4** | **3.3** | **+1.0** | **−0.1** |
| behavioral_pattern (0.15) | 3.5 | 4.8 | 4.3 | +1.3 | +0.8 |
| faithfulness (0.10) | 4.4 | 4.6 | 4.8 | +0.2 | +0.4 |

**Key findings:**

- **Trajectory is the biggest gap between A and B (+1.3 raw points).** A produces profiles that describe what topics appear; B reliably names what the user is building toward. This is the highest-weight dimension (0.30) — it explains most of the overall score gap.
- **Depth asymmetry: B +1.0 over A, C is flat vs A (−0.1).** The explicit checklist instruction in B forces the model to name the contrast between deep and shallow domains. C's briefing framing does not reliably surface this — the judge often can't find a named contrast in C's profiles.
- **Behavioral pattern: both B and C substantially outperform A (+1.3 and +0.8).** The enriched activity string (save/read separation, "backlog signal" label in the activity text itself) contributes to this — both variants receive the same richer format.
- **Specificity is already reasonable in A (4.0) and improves only modestly in B/C (+0.6/+0.5).** A's baseline prompting already yields sub-domain focus for most cases; the explicit specificity rule in B tightens it further but isn't the primary driver.
- **Faithfulness is high across all variants (4.4–4.8).** The richer activity string may actually help here — the model has more concrete evidence to ground claims against, reducing the temptation to invent.

---

## 7. Per-Case Table

Weighted score per case (pass threshold: 0.70):

| Case | A | B | C | B−A | C−A | Notes |
|------|---|---|---|-----|-----|-------|
| heavy_saver_no_reads | 0.450 | 0.725 ✓ | 0.650 | **+0.275** | +0.200 | A: no burst pattern. B: names anxiety-saving. C: names backlog but misses burst timing |
| deep_reader_narrow | 0.650 | **1.000** ✓ | 0.738 ✓ | **+0.350** | +0.088 | A: no trajectory (score 1). B: perfect. C: misses depth_asymmetry (no contrast available — uniformly deep) |
| topic_shift | 0.850 ✓ | 0.850 ✓ | 0.850 ✓ | +0.000 | +0.000 | All variants score equally. A already handles temporal contrast well when the signal is strong |
| intent_reading_list | 0.887 ✓ | **1.000** ✓ | 0.800 ✓ | +0.113 | **−0.087** | C loses specificity (scores 3) — briefing framing says "tech interviews" not "forward-deployed AI engineering" |
| technical_deep_news_skim | 0.812 ✓ | **1.000** ✓ | 0.812 ✓ | +0.188 | +0.000 | A handles asymmetry; B names trajectory (study progression to CRDTs) that A misses |
| backlog_accumulator | 0.487 | 0.875 ✓ | 0.575 | **+0.388** | +0.087 | A: depth_asymmetry=1, behavioral_pattern=4. B: names burst pattern and quest-for-clarity behavioral signal |
| re_reader | 0.637 | 0.875 ✓ | 0.625 | +0.238 | **−0.012** | A: behavioral_pattern=1 (no re-engagement signal). C: trajectory=1 (no re-reading narrative). B catches both |
| eclectic_browser | 0.225 | 0.388 | 0.375 | +0.163 | +0.150 | All fail. B invents a pop-music focus (specificity=1). C invents "technology, culture, identity" theme. Neither resists false coherence |
| synthesizer | 0.688 | 0.925 ✓ | 0.875 ✓ | +0.238 | +0.188 | A: behavioral_pattern=2, misses annotation-while-skimming. B and C both name it |
| sparse_new_user | 0.525 | **0.963** ✓ | 0.800 ✓ | **+0.438** | +0.275 | A: trajectory=2, behavioral_pattern=3. B: all 5s except behavioral_pattern=4. C: trajectory=3 only |

---

## 8. Wins and Regressions vs A

### B wins over A (B score meaningfully higher):

**`backlog_accumulator` (+0.388):** A scores depth_asymmetry=1 — it applies a single `reading_velocity='fast'` label and mentions "a potential shift towards deeper engagement" without naming the 90-minute save burst or the 5-day no-follow-through gap. B names both: *"saving articles... reflecting an anxiety about not keeping up"* and *"the sustained backlog signal indicates unresolved interests."* The explicit `behavioral_pattern` instruction forces this.

**`sparse_new_user` (+0.438):** A gives trajectory=2 and invents a learning arc from 2 articles. B correctly names uncertainty (*"potential unresolved interest in related areas"*) and gets trajectory=5 from a grounded claim: *"working toward strong proficiency in machine learning... specifically focused on CNNs."* The specificity instruction ("be specific") produces *"machine learning — convolutional neural networks and their applications in computer vision"* vs. A's *"machine learning, computer vision".*

**`re_reader` (+0.238):** A gives behavioral_pattern=1 — the profile describes topics (transformers, BERT, GPT) but makes no mention of the re-reading pattern, orphan highlights, or synthesis mode. B names all three: *"deep engagement with fewer selected articles, resulting in a substantial number of highlights"* and *"older highlights which suggest they are saving information that they have not yet explored deeply."*

**`heavy_saver_no_reads` (+0.275):** A labels reading_velocity='fast' (wrong — this is browsing) and calls it "a significant backlog." B correctly uses `browsing`, names the anxiety-saving behavioral pattern explicitly, and identifies the "collecting phase." The backlog instruction in B's prompt is directly responsible.

**`deep_reader_narrow` (+0.350):** A produces trajectory=1 — the profile says "focused on context engineering and agent systems" with no forward-looking statement. B produces trajectory=5: *"building advanced AI agents... strong commitment to understanding and applying best practices."* The trajectory dimension instruction alone closes this gap.

### B regressions vs A:

None. B scores ≥ A on every case.

### C regressions vs A:

**`intent_reading_list` (−0.087):** C specificity=3 — *"preparing for tech interviews and improving career development skills"* vs. A's *"career development, AI engineering roles"*. C's briefing framing de-emphasizes precise labeling in favor of actionable advice. The list names ("AI Engineer & Forward Deployed Engineer") appear in C's memory_text but not in `current_focus`. B scores specificity=5 by following the explicit rule.

**`re_reader` (−0.012, negligible):** C trajectory=1 — no re-reading narrative, just *"the user is delving deeply into attention mechanisms."* B trajectory=4 — names preparation to *"implement or evaluate large language models."*

---

## 9. Residual Failure Taxonomy

### `eclectic_browser` — all variants fail (A=0.225, B=0.388, C=0.375)

**Root cause: false coherence injection.** Neither B nor C resists the urge to find a theme. B invents *"cultural analysis of pop music and its socio-political implications"* as the focus (scoring specificity=1 from the judge). C invents *"exploring the intersection of technology, culture, and identity"* (specificity=2). A lists every topic verbatim (specificity=3, but trajectory=1 and depth_asymmetry=1).

The ideal for this case is explicitly acknowledging the absence of a coherent focus. None of the variants do this reliably. The fix: add an explicit instruction to the prompt — *"If the reading activity shows no dominant focus (diverse domains, all shallow, no highlights), state that explicitly. Do not force a coherent focus where none exists."*

**Type: Algorithm gap** — fixable with a targeted prompt addition.

### `heavy_saver_no_reads` — C fails (C=0.650)

**Root cause: depth_asymmetry=1.** C gives reading_velocity='fast' (should be 'browsing') and provides no statement about which domains the user engages deeply vs. skims — because there are no reads to contrast. C doesn't handle the all-saves-no-reads case correctly; it implies some depth from the topics. B handles it by noting explicitly: *"they have yet to delve deeply into these articles, indicating they are still in a collecting phase."*

**Type: Algorithm gap** — C's briefing framing doesn't naturally generate the right handling for zero-read cases.

### `backlog_accumulator` — C fails (C=0.575)

**Root cause: depth_asymmetry=1, trajectory=2.** C labels reading_velocity='deep' despite no articles being read — an outright error. Then gives a vague trajectory: *"possible overwhelm or confusion on where to start."* B correctly uses 'browsing' and names the quest-for-clarity behavioral signal. The briefing framing causes C to offer actionable advice for the next interaction (*"offering guidance on a specific article or summarizing key themes might be appreciated"*) rather than characterizing the user pattern.

**Type: Algorithm gap** — C's frame optimizes for immediate helpfulness, not accurate characterization.

---

## 10. Phase 9 Decision

### Checklist

| Criterion | A | B | C |
|-----------|---|---|---|
| Mean weighted score ≥ 0.70 | ✗ 0.621 | ✓ 0.860 | ✓ 0.710 |
| Primary metric improvement ≥ 0.05 over baseline | — | ✓ +0.239 | ✓ +0.089 |
| No hard fail regressions | ✓ | ✓ | ✓ |
| No guard rail regressions (cases that were passing in A still pass) | — | ✓ | ✓ (1 tie) |
| Hypothesis confirmed | — | ✓ | partial |

### Recommendation: **SHIP Variant B**

All criteria pass. B improves mean weighted score by +0.239 over A (from 0.621 to 0.860), raises pass rate from 30% to 90%, and regresses on zero cases that were passing in A.

C clears the pass threshold (0.710) and outperforms A, but loses to B on `intent_reading_list` and `re_reader`. The briefing framing produces good behavioral_pattern scores (it naturally describes what would help a future assistant) but is inconsistent on trajectory and depth_asymmetry — dimensions that require the model to synthesize across the whole activity, not just describe what's interesting about the user.

**Hypothesis verdict:**
- ✓ B outperforms A on specificity and trajectory — confirmed, driven primarily by trajectory (+1.3 raw points)
- ✓ B outperforms A on backlog signal (depth_asymmetry, behavioral_pattern) — confirmed
- ✓ C scores comparably to B on trajectory for strong-signal cases (topic_shift, synthesizer) but regresses on intent_reading_list — confirmed
- ✓ Both B and C outperform A on faithfulness — confirmed (A: 4.4, B: 4.6, C: 4.8); richer activity string provides more grounding

**Residual: eclectic_browser fails all variants.** Before shipping to production, add an explicit "resist false coherence" instruction to the B prompt for the no-dominant-focus case. This is a targeted addition, not a redesign.

### Next steps

1. Add to `_BOOTSTRAP_PROMPT` in [memory.py](content-queue-backend/app/tasks/memory.py):
   > *"If the activity shows no dominant focus — diverse domains, all shallow reads, no highlights — say so explicitly. Do not construct a coherent focus where the data shows none."*
2. Update `evals/memory-consolidation-prompt/baselines.json` with B's scores.
3. Commit prompt update + baselines on this branch.
4. Run `/pre-commit-dev` before merging.

---

## Addendum (2026-09-25): re-run after prompt sync + harness fix

The "resist false coherence" instruction above (next step 1) was applied to
production `_BOOTSTRAP_PROMPT` at some point after this report was written, but
this eval's own copy of the B prompt (`runner.py::_VARIANT_B_PROMPT`) was never
updated to match — so re-running this eval as originally written would have
silently scored the *pre-fix* prompt, not what's actually shipped. Synced
`_VARIANT_B_PROMPT` to match production verbatim before re-running.

**Harness bug found and fixed**: the first re-run attempt hit OpenAI's 30K TPM
org rate limit repeatedly during judge scoring (5 dimension calls × 3 variants
× 10 cases = 150 judge calls in quick succession). `scorer.py`'s error handling
silently recorded a rate-limited dimension as `score=1` (the rubric floor) with
no visible signal in the aggregate table — 21 of 150 dimension scores were
corrupted this way, dragging every variant's mean weighted score down
substantially (B appeared to drop from 0.860 to 0.589, which was rate-limiting
artifact, not a real regression). Added retry-with-backoff to `scorer.py`
(`_RATE_LIMIT_MAX_ATTEMPTS`, `_RATE_LIMIT_RETRY_SLEEP_S`) and re-ran; the clean
run hit 4 rate limits, all recovered via retry, 0 corrupted scores.

**Clean re-run results** (`results/latest.json`, 2026-09-25):

| Variant | Weighted | Pass rate | Hard fail |
|---|---|---|---|
| A | 0.6663 | 50% | 0% |
| B | 0.8412 | 90% | 0% |
| C | 0.7250 | 50% | 0% |

B still ships: +0.175 over A, 90% pass rate, 0 hard fails — same direction and
magnitude as the original decision. Absolute scores differ from the 2026-07-10
baseline for all three variants (A moved too, not just B) — this is expected
LLM-judge run-to-run variance on a 10-case pilot, not a regression signal; per
this eval's own metric-definition note, deltas under ~0.10 on this dataset size
are directional, and A's baseline itself shifted more than that between runs.

**The targeted fix did not fully resolve its target case.** `eclectic_browser`
(B) still fails: weighted 0.475, `trajectory`=2, `depth_asymmetry`=2. The
model's own judge-visible output still constructs a coherent focus
("media branding and identity — sports media, music culture, and societal
perceptions") rather than stating "no dominant focus" as the new instruction
asks for. The instruction exists in the prompt; the model isn't reliably
following it for this case. This is not a instruction-wording problem alone —
worth a follow-up eval cycle testing a stronger/differently-framed version of
the instruction, or accepting this specific pattern (broad, shallow,
multi-domain reading with no highlights) as a residual failure mode rather than
continuing to chase it with prompt tweaks.
