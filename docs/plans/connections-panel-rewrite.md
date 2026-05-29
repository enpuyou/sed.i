---
type: plan
status: active
last_updated: 2026-05-14
consumer: agent
---

# Plan: Connections Panel — Two-Mode Rewrite
Date: 2026-05-14
Status: Approved

## Goal

Replace the current article-scoped ConnectionsPanel with a highlight-centric two-mode panel that matches the approved v13 mock-up (`/tmp/connections-mockup-v13.html`). Mode 1 shows connections for a single clicked highlight (with source note and lazy insight). Mode 2 shows all highlights in the article with their connections as scrollable cards.

## Non-goals

- Semantic tag extraction improvements (Phase 1 of connections-concept-emergence-plan) — separate initiative
- Reading Themes page (Phase 2 of roadmap)
- Mobile support — connections panel is already desktop-only (xl:)
- Insight pre-generation via Celery — insight is generated lazily per request, cached in Redis

## Current state

- `ConnectionsPanel.tsx` — fetches from `findArticleConnections(contentId)` → article-level endpoint that returns all highlights grouped by connected article. Single mode, no highlight-level scoping.
- `GET /search/connections/{highlight_id}` — exists but returns flat `list[HighlightConnectionResponse]` (raw rows, no grouping, no author/domain/shared_tags/source_note).
- `GET /search/connections/article/{content_id}` — groups connections by connected article but not by source highlight, no author/domain.
- `ReaderArticle.tsx` line 1083: ignores the `highlightId` arg passed by `HighlightRenderer` — `onShowConnections={(_highlightId) => { onShowConnections?.(); }}`.
- `Reader.tsx`: `onShowConnections` passed as `() => setShowConnectionsPanel(true)` — no highlight ID threaded through.

## Architecture decisions

**Decision: Per-highlight endpoint response shape**
Options:
1. Keep flat list, add grouping in frontend — complex JS, duplicates backend logic
2. Wrapper object `{source_note, connections: []}` — clean, source_note is not per-connection
**Recommendation: Option 2.** Source note is a single value for the whole response. No redundancy.

**Decision: Mode 2 data shape**
Options:
1. Reuse article-level endpoint, group by source highlight in frontend — `highlight_pairs` has `user_highlight_id` so it's possible, but brittle
2. New endpoint `/search/connections/article/{content_id}/highlights` — returns data already grouped by source highlight
**Recommendation: Option 2.** Frontend stays declarative; grouping logic lives in one place.

**Decision: Insight storage**
Options:
1. DB table `highlight_insights` — survives Redis flush, queryable
2. Redis cache (`insight:{highlight_id}:{article_id}`, TTL 7 days) — zero migration, consistent with `embedding_cache.py` pattern
**Recommendation: Option 2.** No migration needed; insight can regenerate on cache miss without data loss.
**Reversibility: Easy** — add DB table later if durability becomes a requirement.

**Decision: Insight loading timing**
Options:
1. Inline in connections endpoint (sync) — blocks response by 500–800ms per article
2. Lazy per-card (separate fetch after connections load) — cards appear immediately, insights fill in
**Recommendation: Option 2.** Matches approved design ("generating insight…" loading state per card).

**Decision: `c` key state machine**
- Panel closed → open Mode 2 (`activeHighlightId = null`)
- Panel open, Mode 1 (activeHighlightId set) → switch to Mode 2 (`activeHighlightId = null`)
- Panel open, Mode 2 → close panel

`activeHighlightId: string | null` is the single source of truth. When null = Mode 2; when set = Mode 1. `showConnectionsPanel: boolean` stays as the visibility gate.

## Response schemas (contract between phases)

### `GET /search/connections/{highlight_id}` (updated)
```python
class HighlightArticleConnection(BaseModel):
    article_id: str
    article_title: str
    article_author: str | None = None
    article_domain: str           # netloc from original_url, www. stripped
    shared_tags: list[str]        # intersection: source article tags ∩ connected article tags
    passages: list[str]           # 1–2 best matching highlight texts from the connected article

class ConnectionsForHighlightResponse(BaseModel):
    source_note: str | None = None    # Highlight.note from source highlight
    connections: list[HighlightArticleConnection]
```

### `GET /search/connections/article/{content_id}/highlights` (new)
```python
class HighlightWithConnections(BaseModel):
    highlight_id: str
    highlight_text: str
    connections: list[HighlightArticleConnection]  # same type above
```
Response: `list[HighlightWithConnections]` — only highlights with ≥1 connection after tag filter.

### `GET /search/connections/{highlight_id}/insight/{article_id}` (new)
```python
class InsightResponse(BaseModel):
    insight: str | None    # null if generation failed
```

## Phases

---

### Phase 1 — Backend: connection endpoint refactor (P0)

**Goal**: Both new data shapes are available and tested; old article-level endpoint is unchanged.

**Entry criteria**: Tests in `test_connections_api.py` currently pass.

**Changes**:

1. `app/api/search.py`:
   - Add `HighlightArticleConnection` and `ConnectionsForHighlightResponse` Pydantic models
   - Add `HighlightWithConnections` Pydantic model
   - Rewrite `find_highlight_connections` (`GET /search/connections/{highlight_id}`):
     - JOIN `content_items` to get `author`, `original_url`, `tags`
     - Group results by `content_item_id` (connected article)
     - Compute `shared_tags` = sorted(source_article.tags ∩ connected_article.tags)
     - Skip articles with no shared_tags
     - Keep best 1–2 passages per article (highest similarity)
     - Include `source_note` from `source_highlight.note`
     - Return `ConnectionsForHighlightResponse`
   - Add `find_highlight_grouped_connections` (`GET /search/connections/article/{content_id}/highlights`):
     - Load all highlights for the article
     - For each highlight, run per-highlight connection query (same SQL as above)
     - Skip highlights with zero connections after tag filter
     - Return `list[HighlightWithConnections]`
   - Route ordering: register new `article/{content_id}/highlights` BEFORE `{highlight_id}` in router

2. `tests/test_connections_api.py`:
   - Update existing tests to reflect new response shape
   - Add TDD tests (see below)

**TDD behaviors for Phase 1**:

```
Behavior 1 (tracer bullet):
  Given: source highlight in article A (tags=["AI alignment"])
         connected highlight in article B (tags=["AI alignment"])
  When: GET /search/connections/{source_highlight_id}
  Then: response.connections[0].shared_tags == ["AI alignment"]
        response.connections[0].article_author exists (may be None)
        response.connections[0].article_domain is a non-empty string
        response.source_note == source_highlight.note

Behavior 2:
  Given: source highlight in A (tags=["X"]) and connected in B (tags=["Y"])
  When: GET /search/connections/{source_highlight_id}
  Then: response.connections is empty (no shared tags → filtered out)

Behavior 3:
  Given: source highlight in A with note "my thought"
  When: GET /search/connections/{source_highlight_id}
  Then: response.source_note == "my thought"

Behavior 4 (Mode 2 endpoint tracer):
  Given: article with 2 highlights, each connected to different articles via shared tags
  When: GET /search/connections/article/{content_id}/highlights
  Then: response has 2 items, each with highlight_id + highlight_text + connections

Behavior 5:
  Given: article with 1 highlight that has NO connections
  When: GET /search/connections/article/{content_id}/highlights
  Then: response is [] (highlight omitted since no connections)

Behavior 6 (isolation):
  Given: user A and user B each have highlights
  When: user A calls GET /search/connections/{highlight_id}
  Then: response contains only user A's library highlights (no user B data)
```

**Exit criteria**:
- [ ] All 6 TDD behaviors pass
- [ ] `ruff check app/` passes
- [ ] Existing `test_connections_api.py` tests updated and green
- [ ] `pytest tests/ -x -q` fully green

**Estimated scope**: 2 files, ~120 lines changed

---

### Phase 2 — Backend: insight generation endpoint (P1)

**Goal**: Lazy insight fetch per article pair, cached in Redis, rendered as optional text in Mode 1 cards.

**Entry criteria**: Phase 1 complete and passing.

**Changes**:

1. `app/api/search.py`:
   - Add `InsightResponse` Pydantic model
   - Add `generate_highlight_insight` (`GET /search/connections/{highlight_id}/insight/{article_id}`):
     - Build cache key: `insight:{highlight_id}:{article_id}`
     - Redis get → return if cached
     - On miss: load source highlight text + all connected highlight texts for the article
     - Call `OpenAI(api_key=settings.OPENAI_API_KEY)` with gpt-4o-mini
     - Prompt: "In one sentence, explain how the first passage connects to the other passages in terms of shared ideas. Be specific, not generic. Reply with only the sentence."
     - On success: `redis_client.setex(cache_key, 604800, insight_text)` (7 days)
     - On failure: return `{insight: null}` — never 500 to caller
     - Register route BEFORE `/{highlight_id}` pattern to avoid conflict

2. `tests/test_connections_api.py`:
   - Add TDD tests with OpenAI patched

**TDD behaviors for Phase 2**:

```
Behavior 1 (tracer bullet — cache miss):
  Given: monkeypatched openai returning "These passages share the mesa-optimization theme."
  When: GET /search/connections/{highlight_id}/insight/{article_id}
  Then: response.insight == "These passages share the mesa-optimization theme."

Behavior 2 (cache hit):
  Given: Redis already has key insight:{h}:{a} = "Cached insight."
  When: GET /search/connections/{highlight_id}/insight/{article_id}
  Then: response.insight == "Cached insight." (OpenAI NOT called)

Behavior 3 (OpenAI failure):
  Given: OpenAI raises exception
  When: GET /search/connections/{highlight_id}/insight/{article_id}
  Then: response == {insight: null} — 200, not 500

Behavior 4 (auth):
  When: unauthenticated request
  Then: 401
```

**Exit criteria**:
- [ ] All 4 behaviors pass
- [ ] `ruff check app/` passes
- [ ] `pytest tests/ -x -q` fully green

**Estimated scope**: 1 file, ~60 lines added

---

### Phase 3 — Frontend: ConnectionsPanel rewrite + Reader wiring (P0)

**Goal**: Panel renders the v13 design end-to-end; highlight click enters Mode 1; `c` key behavior matches approved state machine.

**Entry criteria**: Phase 1 complete (API shapes confirmed). Phase 2 not required — insight is additive.

**Changes**:

1. `frontend/lib/api.ts` (`searchAPI`):
   - Update `findHighlightConnections` return type to `ConnectionsForHighlightResponse`
   - Add `findHighlightGroupedConnections(contentId)` → `GET /search/connections/article/{content_id}/highlights`
   - Add `getConnectionInsight(highlightId, articleId)` → `GET /search/connections/{highlight_id}/insight/{article_id}`

2. `frontend/components/ConnectionsPanel.tsx` — full rewrite:
   - Props: `{ contentId, activeHighlightId, isOpen, onBackToAll, onNavigateToArticle? }`
   - When `activeHighlightId` is set → Mode 1 (fetch `findHighlightConnections(activeHighlightId)`)
   - When `activeHighlightId` is null → Mode 2 (fetch `findHighlightGroupedConnections(contentId)`)
   - Mode 1 layout (from v13):
     - `panel-top`: `← all highlights` compact button → calls `onBackToAll`
     - `scroll-area` (block layout, no flex): note card (if source_note) → article cards
     - Each article card: two-zone (card-head: title/meta/tags/insight; card-passages: matched passages)
     - Insight: lazy fetch per card after connections load, shows "generating…" monospace placeholder
   - Mode 2 layout (from v13):
     - `scroll-area`: highlight cards
     - Each highlight card: highlight-header (tertiary bg, click → `onSelectHighlight(id)`) + block-sources
   - Loading state: `RetroLoader` centered
   - Error state: `InlineError` with retry
   - Empty state: `EmptyState` for both modes
   - Re-fetch when `activeHighlightId` changes or `isOpen` becomes true

3. `frontend/components/Reader.tsx`:
   - Add `activeHighlightId: string | null` state (init `null`)
   - `c` key: if panel closed → open + Mode 2; if Mode 1 → Mode 2 (`setActiveHighlightId(null)`); if Mode 2 → close
   - Pass `activeHighlightId` and `onBackToAll={() => setActiveHighlightId(null)}` to `ConnectionsPanel`
   - Update `onShowConnections` prop passed to `ReaderArticle`: `(highlightId: string) => { setShowConnectionsPanel(true); setActiveHighlightId(highlightId); }`

4. `frontend/components/ReaderArticle.tsx`:
   - Change `onShowConnections?: () => void` → `onShowConnections?: (highlightId: string) => void`
   - Fix line 1083: `onShowConnections={(highlightId) => { onShowConnections?.(highlightId); }}`

**Exit criteria**:
- [ ] `npx tsc --noEmit` passes
- [ ] `npx eslint . --max-warnings=0` passes
- [ ] Clicking a highlight opens Mode 1 for that highlight
- [ ] `c` key: closed → Mode 2; Mode 1 → Mode 2; Mode 2 → closed
- [ ] "← all highlights" button returns to Mode 2
- [ ] Cards render without content clipping (no flex on scroll container, no overflow:hidden on cards)
- [ ] Insight loads asynchronously per card, shows placeholder while loading
- [ ] Source note shown in Mode 1 when present
- [ ] Author + domain shown in article cards when present

**Estimated scope**: 4 files, ~350 lines total (ConnectionsPanel ~300, others ~50)

---

### Phase 4 — Docs + commit (P0)

**Goal**: Every artifact updated before shipping.

**Changes**:
1. `docs/features/knowledge-connections.md` — update with new two-mode interaction model
2. `ARCHITECTURE.md` — update ConnectionsPanel section to reflect two modes and new endpoints
3. Commit each phase as a separate commit:
   - Phase 1: `feat: connections — per-highlight endpoint with article metadata and tag filtering`
   - Phase 2: `feat: connections — insight generation endpoint with Redis cache`
   - Phase 3: `feat: connections — two-mode panel (Mode 1 single highlight, Mode 2 all highlights)`

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| FastAPI route ordering conflict between `/{highlight_id}` and `/{highlight_id}/insight/{article_id}` | Low | High | Register `/insight/` sub-path before the generic `/{highlight_id}` pattern; verify with test |
| N-query loop for Mode 2 endpoint (one per highlight) is slow for articles with many highlights | Medium | Medium | Cap at 30 highlights per article in query; P95 target < 800ms |
| Redis unavailable in prod → insight endpoint 500s | Low | Low | Wrap Redis access in try/except; fall back to direct generation or return `{insight: null}` |
| `activeHighlightId` state in Reader creates re-render cascade | Low | Low | `useCallback` on all handlers; `ConnectionsPanel` skips fetch if not open |
| OpenAI cost for insight generation (uncached) | Low | Medium | 7-day TTL amortizes quickly; gpt-4o-mini ≈ $0.0001 per call |

## Verification

### Automated
- Backend: `pytest tests/ -x -q` after each phase
- Frontend: `npx tsc --noEmit && npx eslint . --max-warnings=0` after Phase 3

### Manual (Phase 3)
1. Click a highlight in an article with connections → panel opens in Mode 1 showing only that highlight's connections
2. Click "← all highlights" → Mode 2 shows all highlights with connections as cards
3. Press `c` → same as step 2 if panel is closed
4. Press `c` again in Mode 2 → panel closes
5. Press `c` when in Mode 1 → goes to Mode 2 (not close)
6. Click a highlight card header in Mode 2 → transitions to Mode 1 for that highlight
7. Source note block appears above article cards when highlight has a note
8. Author and domain are visible in article card meta line
9. Tags display in `● tag` dot format
10. Insight placeholder appears then resolves to text (test with real data)
11. Cards with multiple passages do not clip (scroll container shows all content)

### Cross-cutting
- Dark mode / sepia: all CSS uses `var(--color-*)` — no raw hex in component
- Empty states: no connections → `EmptyState` in both modes
- Error state: `InlineError` with retry button — does not re-fetch on mount after error

## Open questions

None — design is locked (v13 mock-up approved), API contracts defined above, TDD behaviors listed.
