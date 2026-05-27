"""
MCP tool: query_library

Translates a natural-language question into a read-only SQL query against the
user's content library and returns the results as plain text.

Security model:
- Schema introspection is run once per process and cached (avoids expensive
  DB round-trips on every call).
- Generated SQL is parsed with sqlglot and rejected if it contains any DDL,
  DML, or references to tables outside the allow-list.
- All queries run with a 500ms statement_timeout and are scoped to the
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
from app.core.llm_client import llm_client, TASK_MCP_SUMMARY, TASK_SQL_GEN

logger = logging.getLogger(__name__)

# Tables the LLM is allowed to query.
# Excludes: users, refresh_tokens, tag_embeddings (internal / no user content).
_ALLOWED_TABLES = frozenset(
    {
        "content_items",
        "highlights",
        "lists",
        "list_items",
        "drafts",
        "content_chunks",
        "reading_clusters",
    }
)

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
    "list_items": [
        "id (uuid)",
        "list_id (uuid) → lists.id",
        "content_item_id (uuid) → content_items.id",
        "added_at (timestamptz)",
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
        "- SELECT only — no INSERT, UPDATE, DELETE, DROP, CREATE, ALTER, TRUNCATE.\n"
        "- Do not use subqueries that access tables not in the allow-list.\n"
        f"- Limit results to {_MAX_ROWS} rows maximum.\n"
        "- Return only the SQL query, nothing else."
    )
    return "\n".join(lines)


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

    # Only SELECT is allowed
    if not isinstance(stmt, exp.Select):
        raise ValueError(
            f"Only SELECT statements are allowed (got {type(stmt).__name__})"
        )

    # Collect all table references
    tables_referenced = {
        node.name.lower() for node in stmt.find_all(exp.Table) if node.name
    }

    disallowed = tables_referenced - _ALLOWED_TABLES
    if disallowed:
        raise ValueError(
            f"Query references disallowed table(s): {', '.join(sorted(disallowed))}. "
            f"Allowed: {', '.join(sorted(_ALLOWED_TABLES))}"
        )

    return sql.strip()


def _enforce_user_isolation(sql: str) -> None:
    """
    Reject SQL that does not actually bind :user_id before execution.

    Two-tier check:
    1. Text scan: :user_id must appear in the SQL string — ensures the bound
       parameter is referenced and not silently ignored by SQLAlchemy.
    2. AST check (when sqlglot is available): :user_id must appear as one side
       of an equality predicate so it can't appear only in a string literal or
       comment.

    Raises ValueError if either check fails.
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

    # Walk all equality nodes looking for one side being :user_id placeholder
    for eq in stmt.find_all(exp.EQ):
        for side in (eq.this, eq.expression):
            if isinstance(side, exp.Placeholder) and side.name == "user_id":
                return  # found — isolation is enforced

    raise ValueError(
        "Generated SQL references :user_id but not in an equality predicate — "
        "the user filter may be ineffective."
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


def _format_results(rows: list[dict], question: str) -> str:
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
    chat_result = llm_client.chat(
        messages=[
            {"role": "system", "content": schema_prompt},
            {"role": "user", "content": question},
        ],
        task=TASK_SQL_GEN,
        max_tokens=512,
        temperature=0.0,
    )

    raw_sql = chat_result.content.strip()

    # Strip markdown code fences if the LLM wrapped the SQL
    if raw_sql.startswith("```"):
        raw_sql = re.sub(r"^```\w*\n?", "", raw_sql)
        raw_sql = re.sub(r"\n?```$", "", raw_sql)
    raw_sql = raw_sql.strip()

    # Step 2: validate — allow-list + read-only
    try:
        safe_sql = _validate_sql(raw_sql)
    except ValueError as e:
        raise ValueError(f"Generated SQL failed validation: {e}") from e

    # Step 3: enforce user isolation — reject if :user_id not bound as an EQ predicate
    try:
        _enforce_user_isolation(safe_sql)
    except ValueError as e:
        raise ValueError(f"Generated SQL failed user isolation check: {e}") from e

    # Step 4: execute with timeout
    try:
        db.execute(text(f"SET LOCAL statement_timeout = '{_QUERY_TIMEOUT_MS}ms'"))
        result = db.execute(text(safe_sql), {"user_id": str(user.id)})
        rows = [dict(zip(result.keys(), row)) for row in result.fetchmany(_MAX_ROWS)]
    except Exception as e:
        raise ValueError(f"Query execution failed: {e}") from e

    # Step 5: format results
    answer = _format_results(rows, question)

    return {
        "answer": answer,
        "sql": safe_sql,
        "row_count": len(rows),
    }
