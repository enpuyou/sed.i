#!/bin/bash
# dev_worker.sh — start Celery worker with auto-reload on code changes
#
# Uses watchfiles to monitor app/ for changes and restart the worker automatically.
# This means you never need to manually restart after editing extraction.py or any task.
#
# Usage: cd content-queue-backend && ./scripts/dev_worker.sh

set -e
cd "$(dirname "$0")/.."

echo "Starting Celery worker with auto-reload (watching app/)..."
echo "Worker will restart automatically when Python files change."
echo ""

# Use pyenv if available (e.g. local macOS dev), otherwise fall through to
# whatever `poetry` is on PATH (CI, Linux, nix, etc.)
if command -v pyenv > /dev/null 2>&1; then
  PYENV_VERSION=3.11.7 pyenv exec poetry run \
    watchfiles \
      --filter python \
      "celery -A app.core.celery_app worker --loglevel=info --concurrency=1 --pool=solo --beat" \
      app/
else
  poetry run \
    watchfiles \
      --filter python \
      "celery -A app.core.celery_app worker --loglevel=info --concurrency=1 --pool=solo --beat" \
      app/
fi
