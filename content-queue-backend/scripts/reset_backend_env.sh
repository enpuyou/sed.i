#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "$BACKEND_DIR"

echo "[reset-env] backend dir: $BACKEND_DIR"

if [[ -n "${VIRTUAL_ENV:-}" ]]; then
  echo "[reset-env] active shell env: $VIRTUAL_ENV"
fi

echo "[reset-env] removing poetry-registered envs for this project"
poetry env remove --all || true

echo "[reset-env] removing local .venv"
rm -rf .venv

echo "[reset-env] reinstalling dependencies"
poetry install --with dev

echo "[reset-env] poetry environment path"
poetry env info -p

echo "[reset-env] uvicorn path"
poetry run which uvicorn

echo "[reset-env] done"
