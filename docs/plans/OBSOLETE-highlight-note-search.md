---
type: plan
status: archived
last_updated: 2026-05-08
consumer: agent
---

# Plan: Highlight + Note Search
Date: 2026-05-08
Status: Draft

## Goal
Allow users to search their highlights and notes from the same search bar that already searches articles. Results should surface the matched highlight text and a link to its parent article, appearing as a distinct section alongside article results.

## Non-goals
- Does not replace the existing article search — it extends it
- Does not add highlight editing from the search results UI
- Does not change the connections panel (separate initiative)
- Does not add semantic highlight search in this phase (keyword/tsvector only — pgvector semantic can be Phase 2)

## Current state
`highlights.text` and `highlights.note` are plain text columns with no search index. `highlights.embedding` (Vector 1536) exists for pgvector similarity but is only used for the connections panel. The main hybrid search (`/search/semantic`) only queries `content_items`. `SearchModal` only renders article-shaped results. The frontend has no concept of a highlight result type.

---

## Architecture decisions

**Decision**: Where to surface highlight results in the search response
**Options considered**:
1. New endpoint `GET /search/highlights` — completely separate, frontend makes two parallel calls
2. Extend `/search/semantic` to return a union `{ articles: [...], highlights: [...] }`
3. Add a separate `type: "article" | "highlight"` discriminator to the existing result list
**Recommendation**: Option 2 — clean separation in the response, frontend renders two sections, no type discrimination complexity. Existing callers (SearchBar dropdown which only shows 5 results) can ignore the `highlights` field or show a subset.
**Reversibility**: Easy — adding a field to the response is non-breaking.

**Decision**: Keyword vs semantic for highlight search in Phase 1
**Options considered**:
1. tsvector + GIN index (keyword only) — fast, no API cost, immediate results
2. pgvector cosine (semantic) — needs embedding at query time (OpenAI call), already exists per highlight
3. Both (hybrid)
**Recommendation**: Option 1 first. Highlights are short snippets — keyword search works well for them. The existing `highlights.embedding` can power semantic in Phase 2.
**Reversibility**: Easy to add semantic on top later.

---

## Phases

### Phase 1 — Database: tsvector on highlights (Priority: P0)

**Goal**: Add a searchable text vector to the highlights table so keyword search is possible.

**Entry criteria**: None — standalone migration.

**Changes**:
1. New Alembic migration: add `search_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', coalesce(text,'') || ' ' || coalesce(note,''))) STORED` to `highlights`. Add a GIN index on it.
   - Pattern to follow: `alembic/versions/a1b2c3d4e5f6_add_search_vector_to_content_items.py`
   - Use a stored generated column (PostgreSQL 12+) rather than a trigger — simpler and equally performant for a user-scoped library.
2. Verify `make migrate` runs cleanly on a local DB.

**Exit criteria**:
- [ ] Migration runs without error
- [ ] `\d highlights` in psql shows `search_vector` column with a GIN index
- [ ] Inserting a highlight and querying `search_vector @@ to_tsquery('simple', 'foo')` returns it

**Risks**: Generated columns require PostgreSQL 12+. Confirm prod Postgres version.
**Estimated scope**: 1 new migration file (~25 lines).

---

### Phase 2 — Backend: highlight search query + response schema (Priority: P0)

**Goal**: Add a `highlights` field to the `/search/semantic` response containing matched highlight results.

**Entry criteria**: Phase 1 complete.

**Changes**:
1. `content-queue-backend/app/schemas/search.py` (or create it): Add `HighlightSearchResult` schema:
   ```python
   class HighlightSearchResult(BaseModel):
       highlight_id: UUID
       text: str
       note: str | None
       color: str
       content_item_id: UUID
       article_title: str
       score: float
   ```
2. `content-queue-backend/app/schemas/search.py`: Add `SearchResponse` schema wrapping both result types:
   ```python
   class SearchResponse(BaseModel):
       articles: list[SimilarContentResponse]
       highlights: list[HighlightSearchResult]
   ```
3. `content-queue-backend/app/core/hybrid_search.py` (or new `highlight_search.py`): Add `search_highlights(user_id, query, limit)` that runs:
   ```sql
   SELECT h.id, h.text, h.note, h.color, h.content_item_id, c.title,
          ts_rank_cd(h.search_vector, to_tsquery('simple', :tsq)) AS score
   FROM highlights h
   JOIN content_items c ON c.id = h.content_item_id
   WHERE h.user_id = :user_id
     AND c.deleted_at IS NULL
     AND h.search_vector @@ to_tsquery('simple', :tsq)
   ORDER BY score DESC
   LIMIT :limit
   ```
4. `content-queue-backend/app/api/search.py`: Update `GET /search/semantic` to call `search_highlights()` in parallel (via `asyncio.gather` or just sequentially for Phase 1), and return `SearchResponse` instead of `list[SimilarContentResponse]`. Add `include_highlights: bool = True` query param as an escape hatch for clients that only want articles.

**Exit criteria**:
- [ ] `GET /search/semantic?query=foo` returns `{ articles: [...], highlights: [...] }`
- [ ] Highlight results include `article_title` so frontend can link to parent
- [ ] `make lint` passes
- [ ] `pytest tests/test_search_api.py -x -q` passes

**Risks**: Changing the response shape of `/search/semantic` is technically breaking. Mitigation: the SearchBar and SearchModal both access `response.items` currently — verify the existing callers and update them in Phase 3 simultaneously.
**Estimated scope**: ~80 lines across 3 files + 1 new schema file.

---

### Phase 3 — Frontend: render highlight results in SearchModal (Priority: P0)

**Goal**: SearchModal shows a "Highlights" section below article results.

**Entry criteria**: Phase 2 complete, types regenerated.

**Changes**:
1. `frontend/lib/api.ts`: Update `searchAPI.semantic()` to expect `{ articles: [...], highlights: [...] }` instead of an array. Update callers.
2. `frontend/types/` (or `generated.ts` after regeneration): Add `HighlightSearchResult` type.
3. `frontend/components/SearchModal.tsx`: Add a "Highlights" section after the article list. Each result shows: highlight text (truncated to ~100 chars), note (if present), and a link to `/content/${content_item_id}#${highlight_id}`. Use the existing design patterns — `font-mono text-xs` for labels, `var(--color-text-muted)` for metadata.
4. `frontend/components/SearchBar.tsx` (the small navbar dropdown): Either show top 2 highlight results in a separate section, or add `include_highlights=false` to its query. The 5-result dropdown is already tight — start with hiding highlights here, showing them only in the full modal.

**Exit criteria**:
- [ ] Searching for a phrase that appears in a highlight shows it in SearchModal under "Highlights"
- [ ] Clicking a highlight result navigates to the reader at the correct article
- [ ] SearchBar navbar dropdown is unaffected (still shows only article results)
- [ ] `npx tsc --noEmit` clean
- [ ] Design audit: no raw colors, `rounded-none`, font-mono labels

**Estimated scope**: ~60 lines across 3 frontend files.

---

## Verification

### Automated
- `pytest tests/test_search_api.py` — add a test that creates a highlight and verifies it appears in search results
- `npx tsc --noEmit`
- `npx eslint . --max-warnings=0`

### Manual
- Add a highlight on any article with distinctive text
- Search for a word from that highlight text
- Confirm it appears in the "Highlights" section of SearchModal
- Click it — confirm it navigates to the correct article

---

## Open questions
- Confirm PostgreSQL version on prod supports stored generated columns (requires PG 12+)
- Should note content be surfaced in results UI, or just the highlight text? (Notes can be long)
- Phase 2: add semantic highlight search using `highlights.embedding`?
