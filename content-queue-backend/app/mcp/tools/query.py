"""
MCP tool: query_library

Translates a natural-language question into a read-only SQL query against the
user's content library and returns the results as plain text.

Security model:
- Schema introspection is run once per process and cached (avoids expensive
  DB round-trips on every call).
- Generated SQL is parsed with sqlglot and rejected if it contains any DDL,
  DML, or references to tables outside the allow-list — including data-modifying
  CTEs attached to a SELECT (e.g. WITH d AS (DELETE ...) SELECT * FROM d).
- Every user-scoped table in each SELECT block must have its own `user_id =
  :user_id` (or owner_id for lists) equality as a top-level AND conjunct of that
  block's WHERE. Filters under OR/NOT/CASE/functions, in another table's filter or
  in another block do not count. content_items also needs `deleted_at IS NULL`.
- All queries run in a READ ONLY transaction with a 500ms statement_timeout and are scoped to the
  authenticated user's rows via a WHERE clause injected by the caller.
- No raw user input is interpolated into SQL strings — the LLM generates SQL
  from a schema description, and the user's ID is passed as a bound parameter.

The LLM is asked to produce SQL with a user_id = :user_id placeholder.
query_library substitutes the real user ID before execution.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

from sqlalchemy.orm import Session
from sqlalchemy import text

from app.models.user import User
from app.core.llm_client import (
    llm_client,
    braintrust_span,
    TASK_MCP_SUMMARY,
    TASK_SQL_GEN,
)

logger = logging.getLogger(__name__)

# Tables the LLM is allowed to query.
# Excludes: users, refresh_tokens, tag_embeddings (internal / no user content).
_ALLOWED_TABLES = frozenset(
    {
        "content_items",
        "highlights",
        "lists",
        "content_list_membership",  # actual junction table (not list_items)
        "drafts",
        "content_chunks",
        "reading_clusters",
    }
)

# Tables that carry a user-scoping column and require a direct WHERE filter.
# Bridge tables (content_list_membership) have no user column and are exempt —
# they must be joined to an already-filtered table to be safely accessed.
_USER_SCOPED_TABLES: dict[str, str] = {
    "content_items": "user_id",
    "highlights": "user_id",
    "drafts": "user_id",
    "content_chunks": "user_id",
    "reading_clusters": "user_id",
    "lists": "owner_id",
}

# Tables with a deleted_at soft-delete column; trashed rows must be excluded.
# Highlights/chunks have no deleted_at of their own — to hide those of a trashed
# article, join content_items and require its deleted_at IS NULL.
_SOFT_DELETE_TABLES = frozenset({"content_items"})

# Columns to expose per table (avoids leaking internal fields like embeddings).
_TABLE_SCHEMA = {
    "content_items": [
        "id (uuid)",
        "title (text)",
        "description (text)",
        "original_url (text)",
        "content_type (text: article|pdf|video|social)",
        "author (text)",
        "tags (text[])",
        "auto_tags (text[])",
        "word_count (int)",
        "reading_time_minutes (int)",
        "is_read (bool)",
        "is_archived (bool)",
        "published_date (timestamptz)",
        "created_at (timestamptz)",
        "processing_status (text)",
        "deleted_at (timestamptz) — trashed when not null; always filter WHERE deleted_at IS NULL",
        "user_id (uuid) — always filter WHERE user_id = :user_id",
    ],
    "highlights": [
        "id (uuid)",
        "content_item_id (uuid) → content_items.id",
        "text (text)",
        "note (text)",
        "created_at (timestamptz)",
        "user_id (uuid) — always filter WHERE user_id = :user_id",
    ],
    "lists": [
        "id (uuid)",
        "name (text)",
        "description (text)",
        "created_at (timestamptz)",
        "owner_id (uuid) — always filter WHERE owner_id = :user_id",
    ],
    "content_list_membership": [
        "content_item_id (uuid) → content_items.id",
        "list_id (uuid) → lists.id",
        "added_at (timestamptz)",
        "added_by (uuid) → users.id",
        "NOTE: no user column — join to lists (owner_id = :user_id) or content_items (user_id = :user_id)",
    ],
    "drafts": [
        "id (uuid)",
        "list_id (uuid) → lists.id",
        "content (text)",
        "updated_at (timestamptz)",
        "user_id (uuid) — always filter WHERE user_id = :user_id",
    ],
    "content_chunks": [
        "id (uuid)",
        "content_item_id (uuid) → content_items.id",
        "chunk_index (int)",
        "text (text)",
        "user_id (uuid) — always filter WHERE user_id = :user_id",
    ],
    "reading_clusters": [
        "id (uuid)",
        "label (text)",
        "item_ids (uuid[])",
        "user_id (uuid) — always filter WHERE user_id = :user_id",
    ],
}

_MAX_ROWS = 50
_QUERY_TIMEOUT_MS = 500

# DML/DDL node types forbidden anywhere in the AST — including inside CTEs.
# Checking only the root node misses patterns like:
#   WITH d AS (DELETE FROM highlights WHERE user_id = :user_id RETURNING *)
#   SELECT * FROM d
_FORBIDDEN_NODE_TYPES: tuple = ()  # populated lazily after sqlglot import


@lru_cache(maxsize=1)
def _build_schema_prompt() -> str:
    """
    Build the schema description injected into the system prompt.

    Cached after the first call — schema doesn't change at runtime.
    """
    lines = ["You have access to a PostgreSQL database with these tables:\n"]
    for table, columns in _TABLE_SCHEMA.items():
        lines.append(f"Table: {table}")
        for col in columns:
            lines.append(f"  - {col}")
        lines.append("")
    lines.append(
        "Rules:\n"
        "- Only use tables from the list above.\n"
        "- Always filter by user_id = :user_id (or owner_id = :user_id for lists).\n"
        "- Every user-scoped table must have its own direct WHERE filter — do not\n"
        "  rely on a filter on one table to scope another in a JOIN or cross-join.\n"
        "  The filter must be AND-ed at the top level of that query block's WHERE\n"
        "  (never under OR, NOT, CASE or a function), and each CTE/subquery needs\n"
        "  its own filters.\n"
        "- content_items must also have WHERE deleted_at IS NULL (trashed rows are\n"
        "  hidden). When reading highlights or content_chunks, join content_items\n"
        "  and filter it the same way so trashed articles' highlights stay hidden.\n"
        "- No UNION/INTERSECT/EXCEPT; the query must be a single plain SELECT.\n"
        "- SELECT only — no INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE.\n"
        "- Do not use subqueries that access tables not in the allow-list.\n"
        f"- Limit results to {_MAX_ROWS} rows maximum.\n"
        "- Return only the SQL query, nothing else."
    )
    return "\n".join(lines)


def _get_forbidden_node_types():
    """Return sqlglot DML/DDL node types (imported lazily)."""
    global _FORBIDDEN_NODE_TYPES
    if _FORBIDDEN_NODE_TYPES:
        return _FORBIDDEN_NODE_TYPES
    import sqlglot.expressions as exp

    _FORBIDDEN_NODE_TYPES = (
        exp.Insert,
        exp.Update,
        exp.Delete,
        exp.Drop,
        exp.Create,
        exp.Alter,
        exp.TruncateTable,
        exp.Grant,
    )
    return _FORBIDDEN_NODE_TYPES


def _ancestors(node, kind):
    """Yield enclosing nodes of the given type, innermost first."""
    parent = node.parent
    while parent is not None:
        if isinstance(parent, kind):
            yield parent
        parent = parent.parent


def _validate_sql(sql: str) -> str:
    """
    Parse the SQL with sqlglot and enforce the allow-list + read-only rules.

    Returns the cleaned SQL string on success.
    Raises ValueError with a human-readable reason on failure.
    """
    try:
        import sqlglot
        import sqlglot.expressions as exp
    except ImportError:
        # sqlglot not installed — fall back to regex validation
        return _validate_sql_regex(sql)

    try:
        statements = sqlglot.parse(sql, dialect="postgres")
    except Exception as e:
        raise ValueError(f"SQL parse error: {e}") from e

    if not statements or len(statements) > 1:
        raise ValueError("Exactly one SQL statement is required")

    stmt = statements[0]

    # Root must be SELECT — catches obvious DML
    if not isinstance(stmt, exp.Select):
        raise ValueError(
            f"Only SELECT statements are allowed (got {type(stmt).__name__})"
        )

    # Walk the full AST — catches DML/DDL hidden inside CTEs (e.g.
    # WITH d AS (DELETE ... RETURNING *) SELECT * FROM d).
    forbidden = _get_forbidden_node_types()
    for node in stmt.walk():
        if isinstance(node, forbidden):
            raise ValueError(
                f"Query contains forbidden {type(node).__name__} node. "
                "Data-modifying CTEs and DDL are not allowed."
            )

    # Collect all table references and enforce the allow-list
    # A reference to a CTE is not a table read, but only outside that CTE's own
    # body: inside it the name still resolves to the real table, so a CTE named
    # "users" cannot launder SELECT * FROM users.
    cte_names = {c.alias.lower() for c in stmt.find_all(exp.CTE) if c.alias}
    tables_referenced = set()
    for node in stmt.find_all(exp.Table):
        if not node.name:
            continue
        name = node.name.lower()
        if name in cte_names and not any(
            (c.alias or "").lower() == name for c in _ancestors(node, exp.CTE)
        ):
            continue
        tables_referenced.add(name)
    disallowed = tables_referenced - _ALLOWED_TABLES
    if disallowed:
        raise ValueError(
            f"Query references disallowed table(s): {', '.join(sorted(disallowed))}. "
            f"Allowed: {', '.join(sorted(_ALLOWED_TABLES))}"
        )

    return sql.strip()


def _conjuncts(expr) -> list:
    """Flatten a predicate into its top-level AND conjuncts (unwrapping parens)."""
    import sqlglot.expressions as exp

    if expr is None:
        return []
    if isinstance(expr, exp.Paren):
        return _conjuncts(expr.this)
    if isinstance(expr, exp.And):
        return _conjuncts(expr.this) + _conjuncts(expr.expression)
    return [expr]


def _block_conjuncts(select) -> list:
    """
    Predicates that unconditionally restrict the rows of one SELECT block:
    top-level AND conjuncts of its WHERE, plus those of the ON clause of any
    INNER join (an inner-join ON filter is equivalent to a WHERE filter; an
    outer-join ON filter is not, so it never counts).
    """
    where = select.args.get("where")
    out = _conjuncts(where.this) if where is not None else []
    for join in select.args.get("joins") or []:
        side = (join.args.get("side") or "").upper()
        kind = (join.args.get("kind") or "").upper()
        if side or kind not in ("", "INNER"):
            continue
        out += _conjuncts(join.args.get("on"))
    return out


def _enforce_user_isolation(sql: str) -> None:
    """
    Reject SQL that does not isolate each user-scoped table to the current user.

    Structural check, per SELECT block (the root, each CTE, each subquery):
    every user-scoped table referenced directly in that block must have an
    equality `alias.<user col> = :user_id` that is a top-level AND conjunct of
    the block's WHERE (or of an INNER JOIN ON). Equalities under OR, NOT, CASE,
    functions, the SELECT list, HAVING or ORDER BY do not count, and a filter in
    one block never satisfies a table in another block. The root must be a plain
    SELECT (UNION/INTERSECT/EXCEPT are rejected).

    Soft-delete tables (_SOFT_DELETE_TABLES: content_items) additionally need a
    top-level `alias.deleted_at IS NULL` conjunct so trashed rows stay hidden.
    Highlights and chunks of a trashed article carry no deleted_at of their own;
    queries over them must join content_items and filter it the same way.

    An unqualified column is credited only when exactly one table in the block
    could own it. Raises ValueError with a human-readable reason on failure.
    """
    if ":user_id" not in sql:
        raise ValueError(
            "Generated SQL must filter by :user_id — the query would otherwise "
            "return rows from all users."
        )

    try:
        import sqlglot
        import sqlglot.expressions as exp
    except ImportError:
        return  # text check above is sufficient without sqlglot

    try:
        stmt = sqlglot.parse_one(sql, dialect="postgres")
    except Exception:
        return  # parse errors already caught in _validate_sql; don't re-raise

    if not isinstance(stmt, exp.Select):
        raise ValueError(
            f"Only a plain SELECT is allowed (got {type(stmt).__name__}); "
            "UNION/INTERSECT/EXCEPT are not permitted."
        )

    for block in stmt.find_all(exp.Select):
        # alias -> table name for tables that belong directly to this block.
        alias_map: dict[str, str] = {}
        for table_node in block.find_all(exp.Table):
            if table_node.find_ancestor(exp.Select) is not block:
                continue
            name = table_node.name.lower()
            alias_map[(table_node.alias or name).lower()] = name

        scoped = {a: t for a, t in alias_map.items() if t in _USER_SCOPED_TABLES}
        if not scoped:
            continue

        def resolve(col, owns) -> str | None:
            """Alias in this block that `col` refers to, or None if unresolvable."""
            qualifier = (col.table or "").lower()
            candidates = [a for a, t in alias_map.items() if owns(t)]
            if qualifier:
                return qualifier if qualifier in candidates else None
            return candidates[0] if len(candidates) == 1 else None

        user_filtered: set[str] = set()
        not_deleted: set[str] = set()
        for pred in _block_conjuncts(block):
            if isinstance(pred, exp.EQ):
                for col, val in (
                    (pred.this, pred.expression),
                    (pred.expression, pred.this),
                ):
                    if (
                        isinstance(col, exp.Column)
                        and isinstance(val, exp.Placeholder)
                        and val.name == "user_id"
                    ):
                        alias = resolve(
                            col,
                            lambda t, c=col.name.lower(): _USER_SCOPED_TABLES.get(t)
                            == c,
                        )
                        if alias:
                            user_filtered.add(alias)
            elif (
                isinstance(pred, exp.Is)
                and isinstance(pred.this, exp.Column)
                and pred.this.name.lower() == "deleted_at"
                and isinstance(pred.expression, exp.Null)
            ):
                alias = resolve(pred.this, lambda t: t in _SOFT_DELETE_TABLES)
                if alias:
                    not_deleted.add(alias)

        for alias, tbl in scoped.items():
            if alias not in user_filtered:
                user_col = _USER_SCOPED_TABLES[tbl]
                raise ValueError(
                    f"Table '{tbl}' (alias '{alias}') is user-scoped but has no direct "
                    f"top-level WHERE {user_col} = :user_id predicate (AND-ed, not under "
                    "OR/NOT/CASE) in its own query block."
                )
            if tbl in _SOFT_DELETE_TABLES and alias not in not_deleted:
                raise ValueError(
                    f"Table '{tbl}' (alias '{alias}') needs a top-level "
                    "WHERE deleted_at IS NULL predicate to exclude trashed rows."
                )


def _validate_sql_regex(sql: str) -> str:
    """Regex fallback when sqlglot is not installed."""
    upper = sql.upper()
    forbidden_keywords = [
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "CREATE",
        "ALTER",
        "TRUNCATE",
        "GRANT",
        "REVOKE",
        "EXECUTE",
        "CALL",
    ]
    for kw in forbidden_keywords:
        if re.search(rf"\b{kw}\b", upper):
            raise ValueError(f"SQL contains forbidden keyword: {kw}")

    # Table allow-list check via regex
    table_pattern = re.compile(r"\bFROM\s+(\w+)|\bJOIN\s+(\w+)", re.IGNORECASE)
    for match in table_pattern.finditer(sql):
        table = (match.group(1) or match.group(2)).lower()
        if table not in _ALLOWED_TABLES:
            raise ValueError(
                f"Query references disallowed table: {table}. "
                f"Allowed: {', '.join(sorted(_ALLOWED_TABLES))}"
            )

    if not sql.strip().upper().startswith("SELECT"):
        raise ValueError("Only SELECT statements are allowed")

    return sql.strip()


def _format_results(rows: list[dict], question: str, user_id: str) -> str:
    """
    Ask the LLM to summarize query results into a natural-language answer.

    Falls back to a plain table if the LLM call fails or no results.
    """
    if not rows:
        return "No results found for your query."

    headers = list(rows[0].keys())
    table_lines = [" | ".join(headers)]
    table_lines.append("-" * len(table_lines[0]))
    for row in rows[:_MAX_ROWS]:
        table_lines.append(" | ".join(str(row.get(h, "")) for h in headers))
    table_text = "\n".join(table_lines)

    if len(rows) > 20:
        # For large result sets, return the table directly without LLM summarization
        suffix = (
            f"\n\n({len(rows)} rows — result may be truncated)"
            if len(rows) >= _MAX_ROWS
            else ""
        )
        return table_text + suffix

    try:
        with braintrust_span(
            "query_library_summarize",
            input={"question": question, "row_count": len(rows)},
            metadata={"user_id": user_id},
        ):
            result = llm_client.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a reading assistant. The user asked a question about "
                            "their reading library and you ran a database query for them. "
                            "Summarize the results concisely in plain English. "
                            "Be specific — mention titles, counts, or dates from the data."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Question: {question}\n\n"
                            f"Query results:\n{table_text}\n\n"
                            "Summarize these results in 1-3 sentences."
                        ),
                    },
                ],
                task=TASK_MCP_SUMMARY,
                max_tokens=300,
                temperature=0.3,
                user_id=user_id,
            )
        return result.content
    except Exception as e:
        logger.warning(f"Result summarization failed: {e}, returning raw table")
        return table_text


def query_library(*, question: str, user: User, db: Session) -> dict:
    """
    Answer a natural-language question about the user's library using SQL.

    Args:
        question: Plain-English question (e.g. "What have I read this week?")

    Returns:
        {
            "answer": str,      # Natural-language summary of results
            "sql": str,         # Generated SQL (for transparency / debugging)
            "row_count": int,
        }

    Raises:
        ValueError: if the generated SQL fails validation or execution.
    """
    schema_prompt = _build_schema_prompt()

    # Step 1: generate SQL
    with braintrust_span(
        "query_library_sql_gen",
        input={"question": question},
        metadata={"user_id": str(user.id)},
    ):
        chat_result = llm_client.chat(
            messages=[
                {"role": "system", "content": schema_prompt},
                {"role": "user", "content": question},
            ],
            task=TASK_SQL_GEN,
            max_tokens=512,
            temperature=0.0,
            user_id=str(user.id),
        )

    raw_sql = chat_result.content.strip()

    # Strip markdown code fences if the LLM wrapped the SQL
    if raw_sql.startswith("```"):
        raw_sql = re.sub(r"^```\w*\n?", "", raw_sql)
        raw_sql = re.sub(r"\n?```$", "", raw_sql)
    raw_sql = raw_sql.strip()

    # Step 2: validate — allow-list + read-only (including DML-CTE detection)
    try:
        safe_sql = _validate_sql(raw_sql)
    except ValueError as e:
        raise ValueError(f"Generated SQL failed validation: {e}") from e

    # Step 3: enforce per-table user isolation
    try:
        _enforce_user_isolation(safe_sql)
    except ValueError as e:
        raise ValueError(f"Generated SQL failed user isolation check: {e}") from e

    # Step 4: execute with timeout
    try:
        db.execute(text(f"SET LOCAL statement_timeout = '{_QUERY_TIMEOUT_MS}ms'"))
        # Defense in depth: even if validation were bypassed, writes fail.
        db.execute(text("SET TRANSACTION READ ONLY"))
        result = db.execute(text(safe_sql), {"user_id": str(user.id)})
        rows = [dict(zip(result.keys(), row)) for row in result.fetchmany(_MAX_ROWS)]
    except Exception as e:
        raise ValueError(f"Query execution failed: {e}") from e

    # Step 5: format results
    answer = _format_results(rows, question, str(user.id))

    return {
        "answer": answer,
        "sql": safe_sql,
        "row_count": len(rows),
    }
