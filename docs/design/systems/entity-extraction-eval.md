# Entity Extraction Prompt Eval: Before vs After

Date: 2026-07-03
Corpus: 61 articles, enpu@example.com account
Before: prompt v1 (entity-types-only priority)
After: prompt v2 (CONCEPT-first priority, noise-exclusion rules)

---

## Executive summary

The revised prompt produces meaningfully better extraction — CONCEPT entities up 62%,
incidental ORG/PERSON entities down ~55%, concept↔concept/tool relations up 650%.

**Retrieval eval result (32 queries, Recall@10):** avg 0.74 → 0.74 (+0.00).
One query improved (`fde_ai_economy` +0.20 via `forward deployed engineer` CONCEPT node).
Zero regressions.

The flat aggregate masks why: better CONCEPT nodes do not pass the entity-lane
similarity threshold (0.55) for vocabulary-distant queries. `enshittification`
(sim=0.28 to "platform decay tech criticism"), `context anxiety` (sim=0.43 to
"agent failure modes context window"), `frictionless life` (sim=0.14) all fail.
The entity lane only wins when the query text closely names the entity — that
constraint applies equally to the new CONCEPT entities.

The extraction improvement is real and valuable for future features (concept dedup,
tag-to-entity promotion, concept graph browsing). It does not improve the current
similarity-threshold-based retrieval path.

---

## Corpus-level metrics

| Metric | Before (v1) | After (v2) | Delta |
|--------|------------|-----------|-------|
| Total entities | 446 | 323 | −123 (−28%) |
| CONCEPT | 85 | **138** | **+53 (+62%)** |
| TOOL | 105 | 70 | −35 (−33%) |
| ORGANIZATION | 120 | **56** | **−64 (−53%)** |
| PERSON | 108 | **45** | **−63 (−58%)** |
| PAPER | 28 | 14 | −14 (−50%) |
| Total relations | 68 | 77 | +9 (+13%) |
| Concept↔Concept/Tool relations | ~8 | **60** | **+52 (+650%)** |
| Articles with ≥1 CONCEPT entity | ~35 | **56** | +21 (+60%) |

The total entity count falling 28% is intentional: fewer incidental entities,
more signal per entity extracted. The 650% increase in concept-to-concept/tool
relations is the primary quality gain — these are the edges that enable
cross-domain graph traversal.

---

## Per-article comparison (10 articles)

### 1. Our obsession with efficiency is costing us our humanity

| | Before | After |
|--|--------|-------|
| Entities | 8 (2 CONCEPT, 3 ORG, 2 TOOL, 0 REL) | 4 (4 CONCEPT, 0 ORG, 0 TOOL, 2 REL) |
| CONCEPT names | `Harbour Bridge`, `Sydney train` | `frictionless life`, `efficiency obsession`, `technological infantilization`, `slow living` |
| Relations | — | `frictionless life` → `slow living`; `technological infantilization` → `efficiency obsession` |
| Noise removed | `Harbour Bridge`, `Sydney train`, `Do Not Disturb`, `HelloFresh`, `Chefgood` | — |

**Verdict: large improvement.** Before: 5 of 8 entities were scene-setting details
with no graph value. After: all 4 entities are the article's actual argument.

---

### 2. Pluralistic: LLMs are slot-machines

| | Before | After |
|--|--------|-------|
| Entities | 6 (0 CONCEPT, 2 ORG, 3 PERSON, 1 PAPER) | 7 (5 CONCEPT, 1 TOOL, 1 PAPER, 2 REL) |
| CONCEPT names | none | `availability heuristic`, `salience heuristic`, `centaur`, `reverse-centaur`, `LLM slot-machine analogy` |
| Relations | `Glyph` → `The Futzing Fraction` (person→paper); `Reg Braithwaite` → `AI companies` | `LLM slot-machine analogy` → `LLM coding assistants`; `availability heuristic` → `LLM coding assistants` |
| Noise removed | `Pluralistic` (the newsletter itself as ORG), `Cory Doctorow` (author byline) | — |

**Verdict: large improvement.** Before: zero conceptual entities extracted from a
cognitively dense article. After: all five core ideas (`availability heuristic`,
`salience heuristic`, `centaur/reverse-centaur`) are extracted as searchable nodes.

---

### 3. Harness design for long-running application development

| | Before | After |
|--|--------|-------|
| Entities | 8 (2 CONCEPT, 4 TOOL, 1 ORG, 1 PERSON) | 5 (4 CONCEPT, 1 TOOL, 1 REL) |
| CONCEPT names | `Generative Adversarial Networks`, `Ralph Wiggum method` | `context anxiety`, `multi-agent generator/evaluator structure`, `harness design`, `evaluator feedback loop` |
| Relations | none | `evaluator feedback loop` → `multi-agent structure` |
| Noise removed | `Dutch art museum` (incidental example), `Prithvi Rajasekaran` (byline) | — |

**Verdict: improvement.** Before: missed `context anxiety` and `harness design` entirely —
the two concepts most likely to bridge this article to related agent engineering articles.
After: all 4 CONCEPTs are extractable nodes.

Gap remaining: `GAN architecture --[inspired design of]--> multi-agent structure` should
be extracted as a relation but wasn't. The inspiration link is stated explicitly in the text.

---

### 4. The Banality of Online Recommendation Culture

| | Before | After |
|--|--------|-------|
| Entities | 7 (1 pseudo-CONCEPT, 3 ORG, 2 PERSON, 1 TOOL) | 5 (0 CONCEPT, 2 ORG, 2 PERSON, 1 PAPER) |
| CONCEPT names | `New York City` (not a concept) | none |
| Relations | none | none |

**Verdict: no improvement; slight regression.** This article discusses recommendation culture,
affiliate marketing, human curation, and algorithmic saturation — all extractable concepts.
Both prompts fail to surface them. The model defaults to people/orgs when the article has a
narrative profile structure (interviews with Tyler Bainbridge, Anu Atluru). The PAPER entity
`Taste is Eating Silicon Valley` is useful but no concept entities appear.

Root cause: the article leads with specific people and companies for several paragraphs
before its conceptual argument develops. The 1,200-word truncation may be cutting off
the conceptual sections.

---

### 5. What Is Claude? Anthropic Doesn't Know, Either

| | Before | After |
|--|--------|-------|
| Entities | 8 (0 CONCEPT, 1 ORG, 2 TOOL, 5 PERSON) | 5 (1 CONCEPT, 1 TOOL, 2 PERSON, 1 ORG) |
| CONCEPT names | none | `interpretability` |
| Relations | none | `Project Vend` → `Claude` |
| Noise removed | `Daniela Amodei` (exec mentioned once), `Emily Bender`, `Marc Andreessen` (quoted), `Joshua Batson` (researcher) | — |

**Verdict: marginal improvement.** PERSON count fell from 5 to 2 — the quote sources
were correctly excluded. But the article's central argument (the black-box problem in AI,
interpretability as a scientific discipline) generated only one CONCEPT node.
Missing: `black-box problem`, `mechanistic interpretability`, `fanboy/curmudgeon typology`.

---

### 6. How Capitalism Turned Mindfulness Into a Productivity Hack

| | Before | After |
|--|--------|-------|
| Entities | 5 (0 CONCEPT, 1 ORG, 3 PERSON, 1 PAPER) | 5 (0 CONCEPT, 1 ORG, 3 PERSON, 1 PAPER) |
| Relations | `Ron Purser` → `McMindfulness` | none |

**Verdict: no change.** The model extracts Ron Purser, Thich Nhat Hanh, Jon Kabat-Zinn,
and Google — all persons and orgs — but none of `corporate mindfulness`, `spiritual bypassing`,
`McMindfulness critique`, or `neoliberal productivity culture`. Same failure mode as
recommendation culture: narrative/profile structure defeats CONCEPT extraction.

---

### 7. Why Is It So Hard to Be Ordinary?

| | Before | After |
|--|--------|-------|
| Entities | 8 (1 pseudo-CONCEPT, 4 PERSON, 2 PAPER, 1 ORG-via-CONCEPT) | 6 (5 CONCEPT, 1 PERSON) |
| CONCEPT names | `Nicole Diver` (a fictional character — mislabel) | `greatness thinking pattern`, `good-enough life philosophy`, `youth sports seriousness`, `cultural pursuit of excellence`, `Aristotle's concept of aretê` |
| Relations | `Immanuel Kant` → `The Good-Enough Life`; `Aristotle` → `Avram Alpert` | `greatness thinking pattern` → `good-enough life philosophy`; `cultural pursuit of excellence` → `good-enough life philosophy`; `Aristotle's aretê` → `cultural pursuit of excellence` |

**Verdict: large improvement.** Before: the model extracted 4 philosophers as PERSON
entities and no CONCEPTs representing the article's argument. After: 5 rich CONCEPT
entities and 3 concept-to-concept relations — exactly the kind of graph edges that
bridge this article to related culture-criticism articles.

---

### 8. Why I Finally Quit Spotify

| | Before | After |
|--|--------|-------|
| Entities | 8 (0 CONCEPT, 1 ORG, 5 PERSON, 0 meaningful REL) | 6 (2 CONCEPT, 2 TOOL, 2 PERSON, 3 REL) |
| CONCEPT names | none | `enshittification`, `corporation-centered design` |
| Relations | none meaningful | `enshittification` → `corporation-centered design`; `Spotify` → `corporation-centered design`; `Spotify` → `enshittification` |

**Verdict: large improvement.** `enshittification` is the primary concept that bridges
this article to `llms_slot_machines`, `banality_recommendation`, and `californian_ideology`
— the cross-domain search scenario where entity wins matter most. Before: not extracted.
After: extracted with 3 relations linking it to its cause and the primary example.

---

### 9. Trustworthy agents in practice

| | Before | After |
|--|--------|-------|
| Entities | 5 (1 CONCEPT, 1 ORG, 3 TOOL) | 5 (2 CONCEPT, 2 TOOL, 1 ORG) |
| CONCEPT names | `prompt injection` | `trustworthy agents`, `prompt injection` |
| Relations | `Claude` → `Claude Code`; `Anthropic` → `Claude` | `Claude Code` → `trustworthy agents`; `prompt injection` → `trustworthy agents` |

**Verdict: minor improvement.** `prompt injection` was already extracted before. Now
`trustworthy agents` is also extracted as a CONCEPT, and the relations show HOW it
connects to the tools — useful for graph traversal from "agent security" queries.

---

### 10. The Year in Slop

| | Before | After |
|--|--------|-------|
| Entities | 8 (0 CONCEPT, 2 ORG, 2 TOOL, 4 PERSON) | 7 (2 CONCEPT, 3 TOOL, 2 ORG) |
| CONCEPT names | none | `Turing test`, `Slop` |
| Relations | `OpenAI` → `Google` (spurious); `ChatGPT` → `Turing test` | `Slop` → `ChatGPT`; `Sora` → `Slop` |

**Verdict: improvement.** `Slop` and `Turing test` are the two concepts that bridge
this article to `llms_slot_machines` and `banality_recommendation`. Before: zero CONCEPT
extraction despite the article being built around these ideas. After: both extracted with
causal relations. PERSON entities (Andrew Cuomo, Donald Trump, Sam Altman) correctly removed.

---

## Summary by article

| Article | Before CONCEPTs | After CONCEPTs | Useful relations | Verdict |
|---------|----------------|----------------|-----------------|---------|
| Efficiency obsession | 2 (noise) | **4 (signal)** | 2 | ✅ Large improvement |
| LLMs / slot machines | 0 | **5** | 2 | ✅ Large improvement |
| Harness design | 2 | **4** | 1 | ✅ Improvement |
| Why ordinary? | 0 | **5** | 3 | ✅ Large improvement |
| Why quit Spotify? | 0 | **2** | 3 | ✅ Large improvement |
| Trustworthy agents | 1 | **2** | 2 | ↑ Minor improvement |
| Year in Slop | 0 | **2** | 2 | ✅ Improvement |
| What Is Claude? | 0 | 1 | 0 | ↑ Marginal |
| Recommendation culture | 0 | 0 | 0 | ❌ No change |
| Mindfulness | 0 | 0 | 0 | ❌ No change |

---

## Failure mode analysis

**Articles where v2 still fails (0 CONCEPT output):**

Both failing articles share a structural pattern: the article leads with several
paragraphs of person/company narrative (interviews, profiles, business history)
before developing its conceptual argument. The 1,200-word truncation used in
`_MAX_TEXT_WORDS` may discard the conceptual sections. This is a separate issue
from the prompt.

Manual verification: "The Banality of Online Recommendation Culture" develops
its argument about `algorithmic saturation`, `human curation as trust signal`,
and `affiliate marketing as editorial corruption` in the second half of the
article, which falls outside the truncation window.

**Mitigation:** Increase `_MAX_TEXT_WORDS` from 1,200 to 2,000–2,500 for concept
extraction, or extract from a position-weighted sample (beginning + middle + end).

---

## Cross-domain bridge potential: new concept nodes

The following CONCEPT entities extracted by v2 are likely to appear in multiple
articles once re-extraction runs across the full corpus (entities upserted into
the shared graph would bridge these articles):

| Concept entity | Expected in articles | Search queries it enables |
|----------------|---------------------|--------------------------|
| `enshittification` | Quit Spotify, LLMs slot machines, Resonant Manifesto | "platform decay", "tech optimism failure", "algorithm harm" |
| `frictionless life` / `efficiency obsession` | Efficiency article, Mindfulness | "convenience culture criticism" |
| `availability heuristic` / `salience heuristic` | LLMs slot machines, possibly music reward | "cognitive bias AI", "LLM reliability" |
| `context anxiety` | Harness design, Lecture 06 | "agent failure modes", "context window" |
| `good-enough life philosophy` | Why ordinary, Mindfulness | "anti-excellence culture", "ordinary life" |
| `Slop` | Year in Slop, LLMs slot machines | "AI content quality decline" |
| `prompt injection` | Trustworthy agents, possibly cybersecurity articles | "agent security risks" |
| `Californian Ideology` | Californian Ideology, Resonant Manifesto | "tech utopianism" |

---

## Remaining prompt gaps

1. **Truncation:** v2 same as v1 — 1,200 words. Narrative articles lose their conceptual
   sections. Fix: increase to 2,500 words or sample across the full text.

2. **Relation scope:** The prompt says "CONCEPT/TOOL entities from your list" but allows
   person→concept and person→paper relations that are less useful for graph traversal.
   These could be excluded without losing signal.

3. **`enshittification` cross-article merge:** "Why I Finally Quit Spotify" extracts
   `enshittification` but "Pluralistic: LLMs slot machines" (which cites Doctorow and
   uses the term) doesn't. Both would merge at the entity level via `upsert_entity()`
   if extracted by the same name — the upsert is case-insensitive exact match.
   The slot-machines article needs re-extraction to surface this concept.
