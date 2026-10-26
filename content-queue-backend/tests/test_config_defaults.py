import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from app.core.config import Settings

LONG_KEY = "k" * 40


def _env(monkeypatch, **kw):
    monkeypatch.delenv("DEBUG", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    monkeypatch.setenv("REDIS_URL", "redis://x")
    for k, v in kw.items():
        monkeypatch.setenv(k, v)


def test_debug_defaults_to_false(monkeypatch):
    _env(monkeypatch, SECRET_KEY=LONG_KEY)
    assert Settings(_env_file=None).DEBUG is False


def test_short_secret_rejected_when_not_debug(monkeypatch):
    _env(monkeypatch, SECRET_KEY="short-key!", DEBUG="false")
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        Settings(_env_file=None)


def test_short_secret_allowed_in_debug(monkeypatch):
    _env(monkeypatch, SECRET_KEY="short-key!", DEBUG="true")
    assert Settings(_env_file=None).DEBUG is True


def test_debug_routes_not_mounted_when_debug_unset():
    env = {k: v for k, v in os.environ.items() if k != "DEBUG"}
    env.update(ENV_FILE="/dev/null", SECRET_KEY=LONG_KEY, PYTHONPATH=".")
    code = (
        "from app.main import app;"
        "print(any(getattr(r,'path','').startswith('/api/test') for r in app.routes))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True
    )
    assert out.stdout.strip().splitlines()[-1] == "False", out.stderr[-500:]
