---
type: plan
status: active
last_updated: 2026-04-02
consumer: agent
---

# sed.i Knowledge System Roadmap

Status: Draft v1 (2026-04-02)
Scope: Translate the "LLM knowledge base" vision into a practical sed.i product roadmap with concrete user use cases, architecture implications, and clear differentiation.

---

## 1) Why this document exists

The external vision is compelling: collect raw sources, let an LLM compile/maintain a wiki, query and generate outputs, and continuously improve knowledge quality.

This document answers three practical questions:

1. What each part means in day-to-day user workflows (not just architecture terms).
2. How current sed.i maps to each part and benefits from it.
3. Where sed.i is already better than the "hacky scripts + local wiki" baseline.

---

## 2) Current state snapshot (sed.i today)

sed.i already has production-grade building blocks:

- URL capture + ingestion pipeline (web app + browser extension + MCP add_content).
- Background extraction (metadata, full text, embeddings) with Celery.
- Semantic search via pgvector and OpenAI embeddings.
- Lists, highlights, and draft writing workflows.
- MCP server with OAuth2.1 + PKCE, usable from Claude Desktop and claude.ai.
- AI list summarization styles (overview, themes, gaps, timeline).

Meaning: sed.i is not a concept prototype. It is already an operational "knowledge intake + retrieval + writing" product foundation.

---

## 3) What the vision means in real user terms

This section translates each concept into user behavior and product requirements.

### 3.1 Data ingest

**What it means technically**
- Normalize many source types (articles, PDFs, repos, images, notes).
- Preserve provenance (where data came from, when fetched, extraction confidence).

**Actual user use case**
- A founder saves 80 links during customer research week.
- They expect all of it to be available later, deduplicated, searchable, and attributable.

**How sed.i relates today**
- Strong on URL/article ingest and extraction.
- Partial on broad multimodal ingest (repos/images as first-class knowledge units).

**Benefit to sed.i if enhanced**
- Higher trust and less rework: users stop manually re-finding sources.

### 3.2 "Compile into wiki" (LLM-maintained knowledge graph)

**What it means technically**
- Convert raw documents into linked knowledge objects: concepts, claims, summaries, backlinks, unresolved questions.
- Keep those objects updated as new data arrives.

**Actual user use case**
- A PM asks: "What have we learned about onboarding friction this quarter?"
- System should return a curated, linked view of evidence, not 50 raw links.

**How sed.i relates today**
- Has summaries and drafts, but not a persistent concept graph / wiki compiler layer.

**Benefit to sed.i if enhanced**
- Moves product from "reading queue" to "institutional memory engine."

### 3.3 IDE/frontend for knowledge work

**What it means technically**
- Users need a place to browse sources, compiled notes, and generated artifacts in one loop.
- Could be in-app, Obsidian-integrated, or both.

**Actual user use case**
- Researcher jumps between source text, generated synthesis, and their draft without context switching tools.

**How sed.i relates today**
- Has app-native reading + writing UI.
- Does not yet provide Obsidian-native sync/export as a first-class workflow.

**Benefit to sed.i if enhanced**
- Reduces workflow fragmentation; better retention for power users.

### 3.4 Q&A over growing corpus

**What it means technically**
- Retrieval + reasoning over user-scoped corpus with quality controls.
- Should support both quick lookup and multi-hop analytical queries.

**Actual user use case**
- "Compare what my saved sources say about LTV payback in B2B vs PLG."

**How sed.i relates today**
- Strong start with semantic search, MCP toolchain, summarize_list, get_draft.
- Needs stronger cross-document synthesis memory beyond list-level summaries.

**Benefit to sed.i if enhanced**
- Becomes daily decision support, not only archive/search.

### 3.5 Output artifacts (markdown, slides, visuals)

**What it means technically**
- Treat generated outputs as durable artifacts with versioning + provenance.
- Support format-specific generation templates (brief, memo, deck, one-pager).

**Actual user use case**
- User asks for investor update draft + supporting evidence table.
- They revise and then save back as canonical project knowledge.

**How sed.i relates today**
- Draft content exists in-app (good foundation).
- Artifact types beyond draft markdown are limited.

**Benefit to sed.i if enhanced**
- Every query compounds into reusable assets; knowledge quality improves over time.

### 3.6 Knowledge linting / health checks

**What it means technically**
- Scheduled checks for stale claims, contradictions, missing citations, orphan concepts.
- Suggest repair actions and optionally auto-fix safe classes of issues.

**Actual user use case**
- Team wiki stays coherent without manual "knowledge gardening" each week.

**How sed.i relates today**
- No dedicated knowledge integrity subsystem yet.

**Benefit to sed.i if enhanced**
- Trust, durability, and lower entropy as corpus scales.

---

## 4) Where sed.i already does better than the Karpathy-style baseline

The post describes a powerful personal workflow. sed.i can exceed it on product and platform qualities.

### 4.1 Production auth + security model

- OAuth2.1 + PKCE for MCP access.
- User-scoped data separation across tools and queries.
- Structured API and transport controls.

Why this is better:
- Safer for real users/teams than ad hoc local scripts and tokens.

### 4.2 Operational reliability

- Background workers, retries, queue-based ingestion, API boundaries.
- Clear separation of fast UI path vs slow extraction/LLM tasks.

Why this is better:
- Predictable behavior under load; easier incident debugging and scaling.

### 4.3 End-to-end product UX

- Reading queue, highlights, lists, writing surface, API + extension + MCP.
- Not just a folder of markdown plus scripts.

Why this is better:
- Lower activation energy for mainstream users; less setup burden.

### 4.4 Structured domain entities

- First-class models for content, highlights, lists, drafts, stats.
- Better than unconstrained note blobs for many retrieval and workflow tasks.

Why this is better:
- Enables richer permissions, analytics, and targeted AI tools.

### 4.5 Multi-interface architecture

- Same underlying data accessible via app UI and MCP tools.

Why this is better:
- Users can stay in-product or operate from LLM clients without duplication.

---

## 5) Realistic roadmap (what to build next, and why)

### Phase 1 (Weeks 1-4): Knowledge primitives and provenance

**Build**
- Add a knowledge entity layer (Concept, Claim, EvidenceLink, Question).
- Add provenance fields to generated outputs (source IDs, generation timestamp, model metadata).
- Add incremental "recompile list/topic" job.

**Use-case win**
- "Show me claims about churn drivers with source evidence" becomes deterministic.

**How sed.i benefits**
- Stronger trust and auditable AI output.

### Phase 2 (Weeks 5-8): Artifact system (compound outputs)

**Build**
- Versioned artifacts: `research_note`, `brief`, `outline`, `draft`.
- Promote generated result to canonical artifact in one click.
- Auto-backlink artifacts to supporting content/highlights.

**Use-case win**
- "Turn this reading list into a board-ready memo" and keep it as living knowledge.

**How sed.i benefits**
- User work compounds instead of disappearing in chat history.

### Phase 3 (Weeks 9-12): Knowledge health checks

**Build**
- Scheduled lint jobs: stale evidence, contradiction detection, orphan concept detection.
- Suggest fix queue (approve/deny actions in UI).

**Use-case win**
- Team can trust that old conclusions are flagged when new evidence conflicts.

**How sed.i benefits**
- Long-term quality moat as corpus size grows.

### Phase 4 (Months 4-6): Ecosystem integrations

**Build**
- Obsidian-compatible export/import (frontmatter + backlinks + media).
- Artifact exports for Marp and shareable markdown bundles.
- Optional local mirror sync for advanced users.

**Use-case win**
- Users can keep sed.i as source of truth while using preferred external tools.

**How sed.i benefits**
- Captures both mainstream and power-user workflows without forcing one IDE choice.

---

## 6) Success metrics (to avoid shipping abstractions)

Track outcomes tied to behavior, not just feature completion.

1. **Knowledge reuse rate**
   - % of generated artifacts referenced again within 14 days.

2. **Grounding quality**
   - % of generated claims with linked source evidence.

3. **Corpus integrity trend**
   - Health-check issue count per 1,000 items over time (should decrease).

4. **Time-to-insight**
   - Median time from user question to saved artifact.

5. **Assistant utility**
   - % of MCP sessions resulting in a durable artifact or data mutation (not just chat output).

---

## 7) Risks and pragmatic mitigations

### Risk: Overbuilding a graph users cannot feel

Mitigation:
- Ship each phase with one visible user flow and one measurable KPI.

### Risk: LLM hallucinations become "canonical knowledge"

Mitigation:
- Require provenance + confidence thresholds before promote-to-canonical.

### Risk: Cost spikes with frequent recompilation

Mitigation:
- Incremental compilation only on changed items; cache intermediate summaries.

### Risk: UX complexity explosion

Mitigation:
- Keep UI minimal: one artifact panel, one health-check queue, one compile trigger.

---

## 8) Product positioning statement

If executed well, sed.i can be positioned as:

"A production-grade personal/team knowledge operating system: ingest anywhere, reason through MCP, and continuously grow trustworthy artifacts from what you read."

Compared to script-based personal wiki setups:

- sed.i wins on security, reliability, multi-interface product UX, and operational scale.
- script-based setups still win on local-file flexibility.

The roadmap above closes that flexibility gap without losing sed.i's product strengths.
