#!/bin/bash
# Celery worker startup script

set -e  # Exit on error

echo "=== Removing old virtualenv ==="
rm -rf .venv

echo "=== Installing main dependencies from poetry.lock ==="
poetry install --no-root --only main

echo "=== Fixing OpenCV dependencies for headless environment ==="
poetry run pip uninstall -y opencv-python opencv-python-headless || true
poetry run pip install opencv-python-headless

echo "=== Pre-caching YOLO model on disk (avoids download on first PDF task) ==="
poetry run python -c "
from app.tasks.extraction_implementations import _get_yolo_model; _get_yolo_model()
" || true

echo "=== Starting Celery worker ==="
exec poetry run celery -A app.core.celery_app worker --loglevel=info --concurrency=2 --pool=solo --beat
