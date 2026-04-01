#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PC="$ROOT_DIR/content-queue-backend/.venv/bin/pre-commit"
ROOT_PC="$ROOT_DIR/.venv/bin/pre-commit"

if [[ -x "$ROOT_PC" ]]; then
  exec "$ROOT_PC" "$@"
fi

if [[ -x "$BACKEND_PC" ]]; then
  exec "$BACKEND_PC" "$@"
fi

if command -v pre-commit >/dev/null 2>&1; then
  exec pre-commit "$@"
fi

echo "pre-commit not found." >&2
echo "Install one of:" >&2
echo "  1) $ROOT_DIR/.venv/bin/python -m pip install pre-commit" >&2
echo "  2) $ROOT_DIR/content-queue-backend/.venv/bin/python -m pip install pre-commit" >&2
exit 1
