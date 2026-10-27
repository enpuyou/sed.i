# 2026-10-26 — Audit fixes

What shipped: security fixes (text-to-SQL isolation, public profile fields, SSRF in URL fetching, rate limits keyed on the user, safer config defaults), a save-race fix (409 instead of 500), full-library dashboard loading, stuck-item recovery, LLM retry and research fixes, a `/ready` probe and a retrieval regression gate.
Product doc: docs/design/product/dashboard-full-library.md
API changes: additive (`/ready`); `/public/u/{username}` now returns only `username` and `full_name`, and a private profile returns 404 instead of 403
Deploy order: backend first; confirm `SECRET_KEY` is at least 32 characters before deploying (the app refuses to start otherwise)
