# 2026-09-25 — Code Review Fixes: Auth, Task Locks, Budget Handling

What shipped: fixed 10 confirmed findings from a full-codebase review — a
cross-user data-isolation gap in the MCP query tool, a CSRF bypass on
logout/refresh, a refresh-token-retry-misclassified-as-theft bug, four
Celery beat tasks missing (or never actually releasing) their overlap
guard, silent budget-exhaustion handling in the research pipeline, MCP
synthesis tools unreachable over the HTTP/OAuth transport, a dead retry
path in research-memory extraction, and ~119 lines of unused request-router
code. Full findings and fixes: see conversation history / PR description.
Product doc: none — this is entirely reliability/security work, no
user-facing feature changed.
API changes: none (no schema/field changes; CSRF enforcement on
`/auth/refresh` and `/auth/logout` is a behavior tightening, not a
contract change — see Backwards Compatibility below).
Deploy order: backend first (see note below on the pre-existing httpOnly
cookie migration this branch also carries).
