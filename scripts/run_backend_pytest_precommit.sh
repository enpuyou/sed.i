#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(git rev-parse --show-toplevel)"
BACKEND_DIR="$ROOT_DIR/content-queue-backend"
PYTHON_BIN="$BACKEND_DIR/.venv/bin/python"
# Use --git-dir (not --show-toplevel) so the lock lives in the real git directory.
# In a worktree, --show-toplevel/.git is a file, not a directory — mkdir would
# always fail and report "already in progress" even when no run is active.
LOCK_DIR="$(git rev-parse --git-dir)/.precommit-pytest-lock"

cleanup_db_sessions() {
  "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1 || true
import psycopg2

try:
    conn = psycopg2.connect(
        "postgresql://postgres:postgres@localhost:5433/postgres",
        connect_timeout=3,
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        """
        SELECT pg_terminate_backend(pid)
        FROM pg_stat_activity
        WHERE datname = 'content_queue_test'
          AND pid <> pg_backend_pid();
        """
    )
    conn.close()
except Exception:
    pass
PY
}

cleanup() {
  cleanup_db_sessions
  rm -rf "$LOCK_DIR"
}

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "Another backend pytest pre-commit run is already in progress." >&2
  echo "If no run is active, remove stale lock: rm -rf $LOCK_DIR" >&2
  exit 1
fi

trap cleanup EXIT INT TERM

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Backend venv python not found at: $PYTHON_BIN" >&2
  echo "Run: cd content-queue-backend && poetry install --with dev" >&2
  exit 1
fi

# Pre-clean stale DB sessions from previously interrupted runs
cleanup_db_sessions

if (( $# == 0 )); then
  # Keep pre-commit fast and stable: run MCP content tools smoke by default.
  # Full backend suite should be run manually or in CI.
  PYTEST_TARGETS=(tests/mcp/test_content_tools.py)
else
  PYTEST_TARGETS=("$@")
fi

cd "$BACKEND_DIR"
PGOPTIONS='-c lock_timeout=5000 -c statement_timeout=180000' \
  "$PYTHON_BIN" -m pytest "${PYTEST_TARGETS[@]}" -q --tb=line -p no:xdist
