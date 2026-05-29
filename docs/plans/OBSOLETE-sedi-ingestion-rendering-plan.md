---
type: plan
status: archived
last_updated: 2026-04-04
consumer: agent
---

# sed.i Ingestion + Rendering Excellence Plan

Status: Draft v1 (2026-04-04)
Scope: Deep assessment and roadmap for ingestion reliability, rendering quality, and content-type-aware UX.

---

## 1) Why this document exists

This plan addresses three core product questions:

1. How confidently can sed.i ingest the web today?
2. How do we render saved content in a way that is often better than the original page?
3. How do we evolve from one generic "text card" queue to content-type-native experiences (articles, PDFs, video, social, mixed media)?

It also compares sed.i against notable patterns in Obsidian Web Clipper/Reader and other reader products, then turns that into concrete implementation phases.

---

## 2) Current state (what sed.i already does well)

### 2.1 Ingestion architecture today

sed.i has three ingestion paths:

- **Web app URL submit**: creates `content_items` with `processing_status='pending'`, then async extraction.
- **Extension path**: sends `pre_extracted_html` + metadata from live DOM (auth/session context), then backend cleans + persists + backfills metadata/embedding.
- **MCP path**: tool-driven add (`add_content`) through the same backend model and extraction queue.

### 2.2 Extraction pipeline strengths

- **Two-phase extraction** for non-extension links:
  - Phase 1: metadata quickly (title/description/thumbnail/author/date).
  - Phase 2: full text via trafilatura XML -> HTML conversion pipeline.
- **Extension has practical anti-paywall failure handling**:
  - Captures content from authenticated browser context.
  - Sends paywall/access restriction hints (`pre_extracted_access_restricted`).
- **Failure normalization exists**:
  - Explicit statuses + `processing_error` taxonomy surfaced in UI.
- **PDF extraction is significantly more advanced than typical read-it-later apps**:
  - Dedicated YOLO/layout-aware extraction and PDF-specific cleanup.
  - Abstract/title handling and body de-duplication logic.

### 2.3 Rendering strengths

- Reader already applies structure/format improvements (header cleanup, summary area, metadata separation).
- Ingested HTML is normalized to support highlighting, search, summaries, and embeddings.
- Type-aware rendering has started (PDF/academic treatment differs from generic article).

---

## 3) Honest answer: "How many websites can we ingest confidently?"

### 3.1 Current answer

We currently **cannot state a truthful single number** (e.g., "we ingest 83% of websites") because the product lacks a production benchmark + telemetry framework that measures extraction quality by domain/class.

### 3.2 What we can say confidently now

- **High confidence**: standard article pages + many long-form sources, especially with extension-assisted capture.
- **Medium confidence**: dynamic pages with heavy JS, irregular layouts, mixed media embeds, soft paywalls.
- **Lower confidence**: highly interactive apps, anti-bot-gated sources, edge-case social pages, and pages where extracted structure is semantically shallow.

### 3.3 Why this is the right product answer

Without benchmark instrumentation, a hard number is guesswork. The correct move is to ship a confidence score framework and report real ingestion reliability by segment.

### 3.4 14-day measurement plan (must-have before public confidence claims)

Build an ingestion benchmark corpus and score it:

- **Corpus**: 500 URLs minimum across classes:
  - News publishers, blogs, docs sites, Substack/newsletters, academic papers, product docs, social/video links.
- **Scoring dimensions** per item:
  - Parse success (yes/no)
  - Structural fidelity (headings, paragraphs, lists, tables)
  - Media fidelity (images/captions/embeds)
  - Metadata quality (title/author/date/thumbnail)
  - Readability quality (noise ratio, duplicate ratio)
- **Outputs**:
  - Domain leaderboard
  - Failure taxonomy dashboard
  - "Confident coverage" defined as weighted score >= threshold (e.g. 0.8)

Result: we can publish "confident ingestion coverage" with evidence, not anecdotes.

---

## 4) Where competitors appear stronger (and what to borrow)

### 4.1 Obsidian Web Clipper / Reader patterns

Observed strengths:

- **Template-driven clipping** (variables, filters, logic) for site-specific output control.
- **Reader mode integrated into clipping workflow**.
- **Interpreter-style extraction customization** (prompt-like transforms).
- **Power-user control over output shape**.

What this means for sed.i:

- sed.i should add configurable clipping/render presets so users can tune extraction on problematic sites.
- sed.i should expose a "post-process pipeline" where users (or vertical profiles) apply transformation rules before save.

### 4.2 Readwise Reader patterns

Observed strengths:

- Strong multi-format positioning (RSS, PDF, YouTube transcript workflows, newsletters).
- Annotation/highlighting as a first-class feature across content types.
- Robust export workflows to external tools (especially Obsidian).

What this means for sed.i:

- Content type must become a product pillar, not just a backend enum.
- Cross-type queue ergonomics and downstream export should be first-class.

### 4.3 Important context where sed.i may already be better

- Strong production API + auth + MCP architecture.
- Better explicit backend error semantics than many consumer reader products.
- Advanced PDF extraction internals already in place.
- Shared data model across app + MCP workflows, not just local note files.

---

## 5) Product vision for ingestion and rendering

### 5.1 North-star statement

"sed.i should ingest heterogeneous internet content reliably, normalize it into high-quality structured reading artifacts, and render each content type in its ideal form while keeping a clean unified queue experience."

### 5.2 Core principles

1. **Extract once, render many ways** (canonical normalized document model).
2. **Type-aware by default** (article != paper != video transcript != social thread).
3. **Quality is measurable** (confidence scores, not vague statuses).
4. **Fallbacks are explicit and user-visible** (partial extraction transparency).
5. **Mixed-feed UX remains simple** (clean global queue + richer detail views).

---

## 6) Architecture enhancements

### 6.1 Introduce a canonical `NormalizedDocument`

Current storage is largely `full_text` HTML + metadata. We need a richer internal representation:

- `blocks[]` with typed nodes (`heading`, `paragraph`, `list`, `table`, `quote`, `figure`, `code`, `embed`, `tweet`, `video`)
- structural graph (`section_id`, parent/child, order)
- source provenance per block (`extractor`, confidence, source span)
- media registry (`asset_id`, origin_url, alt/caption, dimensions)

Keep `full_text` for backward compatibility, but generate it from canonical blocks.

### 6.2 Add extraction strategy tiers

Use a strategy router by URL + content hints:

- **Tier A**: extension-authenticated DOM parse (preferred when available)
- **Tier B**: trafilatura + readability hybrid
- **Tier C**: site-profile extractor for known problematic domains
- **Tier D**: LLM-assisted structure repair (guarded and cached)

Each run produces a confidence report.

### 6.3 Add domain profiles and rule packs

Maintain a versioned `domain_profiles` registry with:

- known noise selectors
- author/date selectors
- paywall/teaser markers
- media embed extraction rules
- anti-duplication transforms

This captures the practical advantage of template-based clippers while preserving sed.i's server-driven architecture.

---

## 7) Rendering strategy: "better than original page"

### 7.1 What "better" should mean in product terms

Better does **not** mean visually identical. It means:

- less noise
- clearer hierarchy
- preserved semantics (lists/tables/code/citations)
- stable typography and spacing
- predictable media placement
- better annotation affordances

### 7.2 Rendering quality standards

For each ingested item, track:

- heading continuity score
- paragraph coherence score
- media/caption linkage score
- duplicate metadata suppression score
- readability/noise score

Renderers should choose the best template per content type + confidence.

### 7.3 Implementation plan for render improvements

1. Build block renderer components (`BlockParagraph`, `BlockFigure`, `BlockCode`, etc.)
2. Introduce per-type layout presets:
   - article: magazine style
   - academic pdf: abstract + section navigator + figure map
   - video: transcript-centric with timestamp links
   - social thread: post chain + source context
3. Add a "view original" and "report bad extraction" loop on every item.

---

## 8) Content type strategy (beyond current enum)

### 8.1 Expand type taxonomy

Current: `article`, `pdf`, `video`, `tweet`, `unknown`.

Proposed:

- `article`
- `academic_paper`
- `documentation`
- `newsletter`
- `social_thread`
- `video`
- `podcast_episode`
- `repository`
- `dataset`
- `reference_page`
- `unknown`

### 8.2 Add `content_profile` (type + intent)

`content_type` alone is insufficient. Add intent/profile fields:

- reading mode (`scan`, `deep_read`, `watch`, `reference`)
- interaction mode (`highlight_text`, `timestamp_notes`, `code_snippets`)
- queue display mode (`compact_card`, `media_card`, `thread_card`)

### 8.3 Queue UX for mixed media

Keep one unified queue, but make cards type-aware:

- article card: title + source + read time + extraction confidence badge
- paper card: title + abstract snippet + figure count + section count
- video card: duration + transcript availability + key moments
- thread card: post count + author + summary

Add quick filters and grouped view:

- `All`, `Text`, `Papers`, `Video`, `Threads`, `Docs`
- optional grouped timeline mode for mixed research sessions

### 8.4 Content type design wiki (v1)

This section defines the rendering philosophy for eight high-priority content
types and what that means for ingestion/modeling.

#### 1) Article / essay

**Rendering philosophy**
- Calm default reading surface.
- Author is prominent, progress is visible, and existing highlights are easy to skim.

**Key UI behaviors**
- Header with title, source, author, reading time.
- Sticky progress indicator.
- Inline highlight markers and jump navigation.

**Ingestion/model implications**
- Strong author/date extraction confidence matters.
- Preserve heading hierarchy and paragraph boundaries.

**Where sed.i is now**
- Already strong. This is the current best-performing type.

#### 2) Recipe

**Rendering philosophy**
- Utility-first layout over prose fidelity.
- Ingredients and steps stay visible together; metadata is operational.

**Key UI behaviors**
- Split layout: ingredients list + step flow.
- Metadata strip: prep time, cook time, servings, difficulty.
- Optional step checklist + timer hooks.

**Ingestion/model implications**
- Detect and extract structured recipe schema where available.
- Normalize ingredient items, quantities, and ordered instructions.

**Where sed.i is now**
- Not first-class yet; currently tends to render as generic article text.

#### 3) Academic paper

**Rendering philosophy**
- Reference-first, not leisure reading.
- Lead with key findings and fast navigation into evidence.

**Key UI behaviors**
- AI key findings summary at top.
- Citation tools (copy citation formats).
- Related papers actions and section/figure navigator.

**Ingestion/model implications**
- Capture title/authors/venue/year robustly.
- Persist section structure, figure references, abstract, citation metadata.

**Where sed.i is now**
- Strong extraction foundation already exists for PDFs/academic style.
- Needs richer citation/reference UX and related-paper linking.

#### 4) YouTube video

**Rendering philosophy**
- Chapters and transcript are primary; player is secondary.
- Value comes from searchable moments and reusable notes.

**Key UI behaviors**
- Chapter list as the main navigator.
- Resume at chapter/timepoint.
- Timestamped highlights on transcript spans.

**Ingestion/model implications**
- Ingest chapter metadata and transcript segments.
- Store timestamp-indexed highlight anchors.

**Where sed.i is now**
- `video` type exists, but reader experience is not yet chapter/transcript-native.

#### 5) Social post / thread

**Rendering philosophy**
- Preserve conversational sequence, context, and references.
- Linked resources become first-class capture opportunities.

**Key UI behaviors**
- Compact thread view (root + replies in order).
- Link extraction panel with one-click save of referenced URLs.

**Ingestion/model implications**
- Model posts as ordered nodes with parent/reply relations.
- Parse and normalize outbound links as related items.

**Where sed.i is now**
- Basic social detection exists (`social` in extraction, `tweet` in frontend typing),
  but thread-native ingestion/rendering is still missing.

#### 6) Podcast

**Rendering philosophy**
- Audio navigation should be insight-centric.
- Key moments and speaker-aware transcript drive retrieval.

**Key UI behaviors**
- AI key moments as TOC.
- Speaker-tagged transcript + timestamp navigation.
- Highlights and notes unified with rest of library.

**Ingestion/model implications**
- Capture episode metadata, chapters, transcript, speaker segments.
- Support timestamped annotations similarly to video.

**Where sed.i is now**
- Not first-class yet; should share infra patterns with video pipeline.

#### 7) PDF / long document

**Rendering philosophy**
- Keep document whole, but navigation makes it tractable.
- Progress should be chapter-aware, not just global percent.

**Key UI behaviors**
- Chapter/section TOC with chapter-level progress.
- "Resume where you left off" at section/page granularity.
- Dense navigation affordances for long-form reading.

**Ingestion/model implications**
- Strong section segmentation and stable anchors.
- Persist chapter-level progress state model.

**Where sed.i is now**
- Extraction is strong; chapter-aware navigation/progress is the main UX gap.

#### 8) Bookmark / reference

**Rendering philosophy**
- Not everything should be treated as a document to finish.
- Optimize for quick return and retrieval, not completion.

**Key UI behaviors**
- Compact reference card/grid presentation.
- "Last opened" and frequency signals over read progress.
- Fast launch + related-item context.

**Ingestion/model implications**
- Lightweight metadata extraction is enough in many cases.
- Add intent/profile flag so it is excluded from "unread guilt" workflows.

**Where sed.i is now**
- This intent is not modeled explicitly yet.

### 8.5 Cross-type coexistence rules (to keep mixed feed clean)

1. Keep one unified queue shell.
2. Use type-specific cards with consistent baseline layout grammar.
3. Default sort remains recency; optional type-grouped views are user-controlled.
4. Progress semantics are type-specific:
   - article/paper/pdf -> read progress
   - video/podcast -> chapter/time progress
   - bookmark/reference -> recency/frequency, no completion pressure
5. Highlights/notes stay globally searchable across all types.

---

## 9) Phased roadmap

### Phase 1 (Weeks 1-3): Measurement + confidence infrastructure

Deliverables:

- benchmark corpus runner
- extraction quality scoring service
- domain-level dashboard
- confidence badge in UI (internal first)

Outcome:

- We can answer "how many websites we ingest confidently" with real numbers.

### Phase 2 (Weeks 4-7): Canonical normalized document model

Deliverables:

- `NormalizedDocument` schema + migration path
- extractor adapters emit blocks
- HTML compatibility layer

Outcome:

- Rendering and downstream AI workflows become type-aware and stable.

### Phase 3 (Weeks 8-11): Type-aware rendering and queue cards

Deliverables:

- renderer component map by block type
- content-type card variants aligned to the 8-type design wiki
- mixed-feed grouping/filtering UX

Outcome:

- Queue becomes much cleaner for mixed media and more useful at a glance.

### Phase 4 (Weeks 12-16): Domain profiles + user clipping controls

Deliverables:

- domain rule packs for top failing sources
- user-level clipping presets
- "repair extraction" action

Outcome:

- sed.i closes much of the practical gap with template-driven clippers.

### Phase 5 (Weeks 17-20): Advanced media support

Deliverables:

- transcript-first video ingestion flow
- richer social thread normalization
- podcast key-moments + speaker transcript flow
- repo/doc structured ingestion pilot

Outcome:

- sed.i becomes truly multi-format, not article-first with exceptions.

---

## 10) Success metrics

### Ingestion quality

- Confident extraction rate by domain/type
- Partial extraction rate (should decline)
- Manual "view original" click rate (proxy for dissatisfaction)
- Retry/repair success rate

### Rendering quality

- Reader dwell time on extracted content vs original URL bounce
- Highlight density (highlights per 1k words)
- "Report bad extraction" volume trend

### Product utility

- Mixed-type queue engagement (filter usage, open rates)
- Type-specific completion rates (video watched %, paper read depth)

---

## 11) Risks and mitigations

### Risk: taxonomy complexity overwhelms UX

Mitigation:

- Keep one queue shell; progressively reveal type-specific details only where needed.

### Risk: normalized model migration breaks existing features

Mitigation:

- Keep `full_text` compatibility layer until all consumers are moved.

### Risk: LLM repair costs spike

Mitigation:

- Use LLM repair only on low-confidence items and cache results by URL hash.

### Risk: domain profile maintenance burden

Mitigation:

- Auto-prioritize profile work from telemetry-driven top failure domains.

---

## 12) Where sed.i can become clearly better than the alternatives

If this plan ships, sed.i can outperform both script-based and app-only competitors on:

1. **Measured reliability** (published confidence by source class)
2. **Type-native reading quality** (not one-size-fits-all article cards)
3. **Production + programmable interface** (app UX + MCP + API)
4. **Extraction transparency** (clear confidence, provenance, and fallback behavior)
5. **High-quality mixed-feed experience** (clean queue despite heterogeneous media)

That combination is a strong moat: practical enough for daily users, structured enough for AI-native workflows, and robust enough for teams.
