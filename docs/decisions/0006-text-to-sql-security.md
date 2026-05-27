# ADR-0006: Text-to-SQL Security Model

**Status:** Accepted
**Layer:** 9 — Text-to-SQL MCP tool

---

## Context

The `query_library` MCP tool translates natural-language questions into SQL queries against the user's content database. This creates a prompt-injection attack surface: a malicious article in the user's library could contain instructions that make the LLM generate a SQL statement that exfiltrates data, modifies records, or drops tables.

The naive approach — trusting LLM-generated SQL with only a prompt warning — is insufficient because prompt instructions alone cannot prevent a sufficiently crafted injection from breaking out of the schema constraints.

---

## Decision

Validate every LLM-generated SQL statement through a four-layer defense before execution:

1. **AST parsing (sqlglot)** — Parse the SQL into an abstract syntax tree using sqlglot with the `postgres` dialect. Reject if parsing fails.
2. **Statement type check** — Only `SELECT` statements are accepted. Any DDL (`CREATE`, `ALTER`, `DROP`) or DML (`INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`) causes immediate rejection.
3. **Table allow-list** — Collect all table references from the AST. Reject if any table is outside the declared allow-list: `content_items`, `highlights`, `lists`, `list_items`, `drafts`, `content_chunks`, `reading_clusters`. Internal tables (`users`, `refresh_tokens`, `tag_embeddings`) are excluded.
4. **User scoping via bound parameter** — The schema prompt instructs the LLM to always include `WHERE user_id = :user_id`. The real user ID is passed as a SQLAlchemy bound parameter — never interpolated into the SQL string. Even if the LLM omits the filter, the worst case is leaking the user's own data to themselves.
5. **Statement timeout** — All queries run with `SET LOCAL statement_timeout = '500ms'`. Long-running analytical queries (full-table scans) are killed automatically rather than blocking the server.

Regex fallback: if sqlglot is unavailable (degraded environment), a keyword-based regex check catches the most common injection patterns. This is defense-in-depth, not the primary gate.

---

## Alternatives considered

**Trust the prompt instructions alone** — Rejected. LLM prompt instructions can be overridden by injected content in user-controlled text (article bodies, highlights, notes). AST validation is not bypassable through prompt injection.

**Parameterized query generation** — The LLM cannot reliably produce parameterized queries; it doesn't know the runtime parameter values. Instead, the LLM generates the SQL structure and user scoping is injected at execution time via `:user_id`.

**Sandboxed execution database** — Would add significant infrastructure complexity (a separate read-only replica or view layer). The allow-list approach achieves the same isolation without operational overhead.

**Read-only Postgres role** — A good complement but not sufficient alone, since it would still allow reading other users' data. The allow-list + bound-parameter pattern is the primary defense; a read-only role would be an additional layer.

---

## Consequences

- All SQL generation errors surface as `ValueError` with a human-readable reason. The MCP tool returns the error as an answer string rather than crashing.
- The schema exposed to the LLM is a static dict in `query.py` — it does not do runtime introspection. Schema changes require a manual update to `_TABLE_SCHEMA`. This is intentional: the allow-list would become stale if it auto-synced to the live schema.
- Column-level filtering (e.g. never expose the `embedding` vector column) is enforced by only listing safe columns in `_TABLE_SCHEMA`. The LLM cannot SELECT unlisted columns because it doesn't know they exist.
- sqlglot is added as a production dependency (`>=25.0.0,<26.0.0`). It has no native dependencies and adds ~3MB to the install.
