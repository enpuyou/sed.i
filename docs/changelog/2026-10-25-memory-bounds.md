# 2026-10-25 — Memory bounds: library queries, downloads, chunk embedding, list paging

What shipped: API and worker memory no longer grow with library or page size. Saving a URL
with 2,000 items in the library went from +1,233 MB to +2 MB, the recommended feed from
558 MB to 14 MB, a 50 MB page in the worker from 1,997 MB to 626 MB. Lists now load 50 items
at a time and page as you scroll. Oversized HTML pages are truncated at 10 MB (still saved,
flagged as a limited extraction); PDFs over 100 MB are rejected.
Product doc: docs/design/product/list-infinite-scroll.md
Results: docs/audits/2026-10-24-memory-fix-results.md
API changes: additive (`content_count` on `GET /lists/{id}`; optional `skip`/`limit` on `GET /lists/{id}/content`)
Deploy order: backend first (migration `a7d3e9c15b42` runs in the pre-deploy step; run
`scripts/check_url_normalization.py` against production beforehand). The frontend falls back to
the loaded item count if `content_count` is missing.
