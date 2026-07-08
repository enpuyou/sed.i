#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(git rev-parse --show-toplevel)"
PYTHON_BIN="$ROOT_DIR/content-queue-backend/.venv/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Backend venv not found — skipping alembic head check" >&2
  exit 0
fi

cd "$ROOT_DIR/content-queue-backend"
HEAD_COUNT=$("$PYTHON_BIN" -m alembic heads 2>/dev/null | grep -c "(head)" || true)

if [[ "$HEAD_COUNT" -ne 1 ]]; then
  echo "ERROR: alembic has $HEAD_COUNT heads (expected 1)." >&2
  echo "Fix: cd content-queue-backend && poetry run alembic merge heads -m 'merge_heads'" >&2
  exit 1
fi
