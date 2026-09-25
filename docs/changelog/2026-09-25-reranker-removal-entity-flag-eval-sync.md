# Reranker Removal, Entity Lane Flag, and Eval Verification — 2026-09-25

Following a review of what recent search/memory/research features actually
prove with data (not just what tests pass), this pass: removed the Cohere
reranker (its own eval never recommended shipping it, and it's the only paid
third-party API in the search path — cut under a hard cost constraint), put
the entity graph search lane behind a feature flag defaulting to disabled
(its own eval found it underperforms a simpler already-shipped alternative),
fixed a data-corrupting rate-limit bug in the memory-consolidation eval
harness and re-verified that feature's prompt fix with clean data, and
confirms the research-brief agent's existing eval stands unchanged.

For each feature below: what problem it claims to solve, the before/after
data, and the decision that follows from it.

---

## 1. Reranker (Cohere Rerank v3.5) — removed

**Problem it claimed to solve:** hybrid search's `mode="auto"` path returns
RRF-fused keyword+semantic results with no second-pass relevance reordering;
a cross-encoder rerank is a well-known precision lever in retrieval systems.

**Before/after (from `evals/reranker/results/report.md`, since deleted with
the rest of `evals/reranker/`):**

| Variant | R@10 | MRR | NDCG@10 |
|---|---|---|---|
| B — chunks, no rerank (pre-existing) | 0.7478 | 0.7873 | 0.6721 |
| E — B + Cohere rerank | 0.7793 | 0.7975 | 0.6949 |

+3.15pp R@10 in aggregate — clears the eval's own 2pp "real signal" bar. But:

- **Guard-rail regression**: `teen_culture_identity`, a case named in advance
  as must-not-regress, went 1.00 → 0.75.
- **Systematic, not random, cancellation**: reranking measurably helped
  concrete/entity-bridge queries and measurably hurt abstract concept-bridge
  queries — the aggregate gain is largely two opposing effects netting out,
  not a uniform improvement.
- The eval's own Phase 9 decision: **"Neither variant meets all four ship
  criteria... Recommendation: investigate, not ship as-is."** It shipped
  anyway, applied unconditionally to every `mode="auto"` search, with no
  query-shape gating (the eval's own proposed fix) and no cost/budget
  enforcement — the only paid third-party API call in the search path, run
  on every default search, completely unmetered against the app's own
  per-user LLM budget system.

**Latency:** not measured by this eval (it measures quality, not latency);
each rerank call adds one external API round-trip per `mode="auto"` search
that no longer happens.

**Decision: removed.** `app/core/reranker.py`, `tests/test_reranker.py`,
`evals/reranker/`, the `COHERE_API_KEY` setting, and the `cohere` dependency
are all gone. `mode="auto"` hybrid search now returns plain RRF-fused
results (equivalent to eval Variant B). Given the product goal shifted to
supporting more users under a hard cost constraint, a paid per-search API
call with a self-cancelling net effect doesn't clear that bar. If reranking
is revisited, the eval's own unexplored fix — gate by query shape via the
existing `search_router.py` classifier, applying reranking only where it's
shown to help — is the starting point, not re-shipping the ungated version.

---

## 2. Entity graph search lane — disabled by default (feature flag)

**Problem it claims to solve:** semantic + keyword search can't bridge two
articles that share no vocabulary but both mention the same named entity
(e.g. two unrelated articles that both discuss "ChatGPT"). The entity lane
adds a third retrieval path over an extracted entity/mention/relation graph
to catch these cross-domain matches.

**Before/after (from `evals/retrieval/results/report.md`, 45-query eval,
still present, unchanged):**

| Variant | R@10 | MRR | NDCG@10 |
|---|---|---|---|
| A — item only | 0.7459 | 0.8276 | 0.6922 |
| B — + chunks | **0.7652** | **0.8310** | **0.7038** |
| D — + entity passthrough (was production) | 0.7581 | 0.7934 | 0.6796 |

Entity lane (D) beats item-only (A) by +1.2pp R@10, but **underperforms
chunks-only (B) by -0.7pp R@10** and loses on MRR/NDCG too — production was
running a configuration objectively worse than a simpler one already in the
same codebase.

**Root-cause investigation (report §11, "Hub cap investigation"):**
threshold and hub-cap adjustments were tested against all 5 regressed
queries and produced **zero R@10 change** on every one. The report's
conclusion: every regression traces to entity *extraction* quality —
fragmented duplicate entities (e.g. "Claude", "Claude.ai", "Claude Code" as
five separate nodes splitting the same signal), missing conceptual entities
for narrative/managerial articles, and genuine vocabulary mismatches between
query language and extracted entity names. None of these are fixable by a
retrieval-time parameter change.

**What is real and working (not thrown out):** extraction, storage, dedup,
HNSW indexing on `entities.embedding`, and the search lane itself are all
implemented and tested — 32/32 entity-graph tests pass against a live DB
(verified in this session). The 4 genuine wins the eval documents (e.g.
`chatgpt_work_impact` A=0.33→D=1.00, a query with literally zero shared
vocabulary between the expected articles) show the mechanism does what it's
designed to do when extraction quality is sufficient.

**Latency:** not separately measured for the entity lane; HNSW indexing on
`entities.embedding` (a different, unrelated improvement) delivers up to 19×
speedup at N=50K — that number is about index infrastructure, not about
whether the entity lane's results are good.

**Decision: disabled by default, not deleted.** Added
`settings.ENTITY_SEARCH_ENABLED: bool = False` (`app/core/config.py`),
following the existing `PREFECT_ENABLED` precedent for the codebase's one
established feature-flag pattern. The `mode="full"` search lane now skips
`_entity_search` entirely — and the embedding computation that only existed
to feed it — unless the flag is explicitly turned on. This preserves all the
working code for future use once extraction quality improves (entity
deduplication for near-duplicate names, richer extraction for
narrative-shaped content), without running an unproven-value lane on every
modal search in the meantime. `docs/design/systems/entity-graph-search.md`
was corrected in the same commit — it previously described stale thresholds,
an incorrect "name-only embedding" root cause for a fix that already shipped,
and cited a document that didn't exist (`hub-cap-investigation.md`).

---

## 3. Memory consolidation prompt — shipped, fix verified with clean data

**Problem it claims to solve:** the nightly memory-consolidation job writes
`user_profiles.memory_text`, a free-form prose summary consumed by
`synthesize_topic` (MCP quick-mode) and the `/memory/profile` endpoint. The
original prompt (baseline "A") produced shallow, topic-only summaries; a
rewritten prompt ("B") asks for trajectory, depth asymmetry, behavioral
pattern, and backlog signal explicitly.

**What changed in this pass:** the eval's own 2026-07-10 report recommended
one targeted follow-up — a "resist false coherence" instruction for users
with no dominant reading focus (the `eclectic_browser` case, which failed
under every variant). That instruction **was already live in production**
(`app/tasks/memory.py::_BOOTSTRAP_PROMPT`), but the eval's own copy of the
prompt (`evals/memory-consolidation-prompt/runner.py`) was never updated to
match — re-running the eval as-is would have silently scored a prompt that
isn't what's actually shipped. Synced the eval's prompt to production
verbatim before re-running.

**Harness bug found and fixed during re-run:** the first re-run attempt hit
OpenAI's 30K TPM rate limit repeatedly during LLM-judge scoring (150 judge
calls across 10 cases × 3 variants × 5 dimensions). The scorer's error
handling silently recorded any rate-limited dimension as `score=1` — the
rubric floor — with no visible signal in the summary output. 21 of 150
dimension scores were corrupted this way, dragging every variant's score
down and producing a misleading result (B appeared to drop from 0.860 to
0.589 — an artifact of rate-limiting, not the prompt). Added
retry-with-backoff to `evals/memory-consolidation-prompt/scorer.py` and
re-ran; the clean run hit 4 rate limits, all recovered via retry, 0
corrupted scores.

**Before/after (clean re-run, `evals/memory-consolidation-prompt/results/latest.json`, 2026-09-25):**

| Variant | Weighted score | Pass rate | Hard fail |
|---|---|---|---|
| A — old baseline prompt | 0.6663 | 50% | 0% |
| **B — production prompt (with fix)** | **0.8412** | **90%** | **0%** |
| C — briefing framing (alternative) | 0.7250 | 50% | 0% |

B still ships: +0.175 over baseline, 90% pass rate, zero hard fails — same
direction and magnitude as the original 2026-07-10 decision. (Absolute
scores differ from that original run — A moved too, not just B — which is
expected LLM-judge run-to-run variance on a 10-case pilot, not a
regression.)

**The targeted fix did not fully resolve its target case.**
`eclectic_browser` under B still fails (weighted 0.475, `trajectory`=2,
`depth_asymmetry`=2). The judge's own reasoning shows the model still
constructs a coherent-sounding focus ("media branding and identity — sports
media, music culture, and societal perceptions") instead of stating "no
dominant focus" as the new instruction asks. The instruction is present in
the prompt; the model isn't reliably following it for this case pattern.

**Decision: prompt stays shipped (already was); fix's limitation now
documented, not silently declared solved.** `baselines.json` and
`results/report.md` updated with the clean re-run numbers and this finding.
Worth a follow-up eval cycle testing a stronger or differently-framed
version of the "resist false coherence" instruction, or accepting this
specific failure mode (broad, shallow, multi-domain reading with no
highlights) as a residual rather than continuing to chase it with prompt
tweaks alone.

---

## 4. Research brief agent — no change, existing verification stands

**Problem it claims to solve:** a single hybrid search over a raw research
question misses multi-angle, cross-cluster, or partial-coverage answers that
a decomposed, multi-subagent research pass can catch — and can't tell the
user what the library *doesn't* cover.

**Existing data (`evals/research-brief/results/report.md`, 2026-07-14,
21-case pilot, not re-run — nothing has changed in this pipeline since that
eval, so re-running would burn LLM budget for no new information):**

| Variant | Avg score | vs A |
|---|---|---|
| A — single-pass baseline (pre-feature) | 0.605 | — |
| B_old — research brief v1 | 0.515 | −0.090 |
| **C — v1 + retrieval fixes + rubric v2 (shipped)** | **0.744** | **+0.139** |

17/21 cases improved vs. baseline, 20/21 vs. the prior agent version, zero
fabricated citations across any run. One known residual: thin-library cases
(1 article covering a sub-topic) sometimes produce a brief that looks
complete but omits the corresponding gap entry — tracked as a non-blocking
follow-up in the original report, not addressed here.

**Decision: no change.** Already shipped on real, clean data; cited here per
the request to have this feature's numbers documented alongside the others,
not because anything moved.

---

## Verification

- Full backend suite: 804 passed, 6 skipped (entity-lane-dependent cases,
  correctly skipped while `ENTITY_SEARCH_ENABLED=False`), 1 pre-existing
  unrelated flake deselected (`context_engineering_direct` — confirmed to
  fail identically before any change in this pass, via `git stash`).
- `ruff check app/` (the `make lint` scope): clean.
- `tsc --noEmit`: clean (no frontend files touched).
- `grep -rn cohere content-queue-backend/ --include="*.py"`: zero remaining
  references.
- Entity-lane tests: 33/33 passing, including a new test confirming
  `_entity_search` is not called when the flag is at its default (`False`).
