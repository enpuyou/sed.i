# Connections Panel UI Overhaul

**Branch:** `feature/connection`
**Date:** 2026-05-15

---

## What shipped

### Panel redesign

- **Transparent panel** — connection cards now float over the canvas without a panel background, consistent with the highlights sidebar
- **Mode 2 compact layout** — all connected highlights listed as cards; each shows article rows beneath sorted by score (strongest match first); highlights appear in document order (by start_offset)
- **Mode 2 → Mode 1 navigation** — clicking an article row in Mode 2 opens Mode 1 scoped to that highlight AND scrolls the panel to that specific connection card
- **Mode 1 auto-scroll** — when Mode 1 opens (via highlight click or Mode 2 article row click), the reader automatically scrolls to the source highlight in the article text
- **Connection score exposed** — each connection card and article row shows the cosine similarity score as a bare decimal (e.g. `0.82`)
- **Matched passages clickable individually** — each passage in Mode 1 navigates to that article at the exact highlight location (`?h=` URL param); "open article →" footer opens the article without a specific target

### Blue connection dot

- Dot now appears on the **first character** of the highlight (was incorrectly placed on the last segment for multi-line highlights)
- Dot stays within line height on hover (was clipping at the top due to negative Y translation)

### Passage text

- Matched passages render in `font-sans` — explicit family set to prevent inheritance from reader font-setting rules

---

## API changes (backwards compatible)

`HighlightArticleConnection` gained two new fields — additive, no breaking change:

| Field | Type | Notes |
|-------|------|-------|
| `passage_highlight_ids` | `list[str]` | Highlight IDs for each matched passage, same order as `passages` |
| `connection_score` | `float` | Top cosine similarity for this article connection (rounded to 3 decimal places) |

Connections no longer require shared semantic tags — the threshold is cosine similarity ≥ 0.3. This surfaces connections between highlights in articles that don't share tag overlap.

---

## Component changes

| File | Change |
|------|--------|
| `frontend/components/ConnectionsPanel.tsx` | Full rewrite — transparent panel, Mode 2 compact rows, Mode 1 card zones, `targetArticleId` scroll |
| `frontend/lib/api.ts` | `HighlightArticleConnection` interface updated with new fields |
| `frontend/components/InlineHighlight.tsx` | `showConnectionIndicator` prop (separate from `showIndicators`); dot position fixed |
| `frontend/components/HighlightRenderer.tsx` | First-segment tracking (`isFirst`) passed as `showConnectionIndicator`; previously only last segment was tracked |
| `frontend/components/ReaderArticle.tsx` | `initialHighlightId` prop — scroll-to-highlight on initial load |
| `frontend/components/Reader.tsx` | Passes `initialHighlightId`; watches `activeHighlightId` to scroll reader on Mode 1 entry |
| `frontend/app/content/[id]/page.tsx` | Reads `?h=` query param and passes to Reader |
| `content-queue-backend/app/api/search.py` | Extended passages tuple to include highlight ID; added `connection_score` to builder; removed shared-tag requirement |

---

## Deploy order

Backend must be deployed before frontend. The new `connection_score` and `passage_highlight_ids` fields are guarded with `?? 0` / optional chaining on the frontend, so a brief window where old backend serves new frontend is tolerable — scores will show `0.00` rather than crashing.
