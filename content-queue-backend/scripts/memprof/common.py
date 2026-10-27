# ruff: noqa: E731
"""Shared helpers: isolated env, process management, memory sampler + watchdog, DB/Redis."""

import atexit
import csv
import os
import re
import signal
import statistics
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import psutil
import psycopg2
import redis

HERE = Path(__file__).resolve().parent
PG_ADMIN_DSN = "postgresql://postgres:postgres@localhost:5433/postgres"
REDIS_URL = "redis://localhost:6379/15"
API_PORT, FAKE_PORT, PAGE_PORT = 8010, 9100, 9200
KILL_MB = 6000
SECRET = "memprof-secret-key-0123456789-0123456789-0123456789"

_PROCS: list = []


def db_dsn(db_name):
    return f"postgresql://postgres:postgres@localhost:5433/{db_name}"


def child_env(backend_dir, db_name, extra=None):
    """Explicit minimal environment. Nothing from the caller's env except PATH/HOME/TMPDIR/LANG."""
    env = {
        k: os.environ[k]
        for k in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "USER")
        if k in os.environ
    }
    tcache = os.environ.get("TIKTOKEN_CACHE_DIR") or os.path.join(
        tempfile.gettempdir(), "data-gym-cache"
    )
    env.update(
        {
            "DATABASE_URL": db_dsn(db_name),
            "REDIS_URL": REDIS_URL,
            "SECRET_KEY": SECRET,
            "OPENAI_API_KEY": "dummy",
            "OPENAI_BASE_URL": f"http://127.0.0.1:{FAKE_PORT}/v1",
            "DEBUG": "true",
            "ENV_FILE": "/nonexistent/memprof.env",  # config.py honours this; .env is never read
            "PYTHONPATH": str(backend_dir),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "PREFECT_ENABLED": "false",
            # pages are served from 127.0.0.1; the SSRF guard would refuse them
            "FETCH_ALLOW_PRIVATE_ADDRESSES": "true",
            "S3_STORAGE_ENABLED": "false",
            "HF_HUB_OFFLINE": "1",
            "TIKTOKEN_CACHE_DIR": tcache,
            # Any accidental outbound call goes to a dead proxy; loopback is exempt.
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "http_proxy": "http://127.0.0.1:9",
            "https_proxy": "http://127.0.0.1:9",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    for k in (
        "COHERE_API_KEY",
        "BRAINTRUST_API_KEY",
        "SENTRY_DSN",
        "POSTHOG_API_KEY",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "RESEND_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_S3_BUCKET",
        "AWS_REGION",
        "DISCOGS_TOKEN",
        "PREFECT_API_URL",
        "PREFECT_API_KEY",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
    ):
        env[k] = ""
    env.update(extra or {})
    return env


class Proc:
    def __init__(self, name, args, cwd, env, logpath):
        self.name, self.args, self.cwd, self.env, self.logpath = (
            name,
            args,
            str(cwd),
            env,
            str(logpath),
        )
        self.p = None

    def start(self):
        self.log = open(self.logpath, "ab")
        self.p = subprocess.Popen(
            self.args,
            cwd=self.cwd,
            env=self.env,
            stdout=self.log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _PROCS.append(self)
        return self

    @property
    def pid(self):
        return self.p.pid

    def alive(self):
        return self.p is not None and self.p.poll() is None

    def log_text(self, offset=0):
        with open(self.logpath, "rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", "replace")

    def log_size(self):
        return os.path.getsize(self.logpath)

    def stop(self, grace=20):
        if self.p is None:
            return
        if self.p.poll() is None:
            try:
                os.killpg(self.p.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.p.wait(grace)
            except subprocess.TimeoutExpired:
                pass
        try:
            os.killpg(
                self.p.pid, signal.SIGKILL
            )  # reap stragglers (beat child, YOLO child)
        except ProcessLookupError:
            pass
        try:
            self.p.wait(5)
        except Exception:
            pass
        try:
            self.log.close()
        except Exception:
            pass
        if self in _PROCS:
            _PROCS.remove(self)

    def kill(self):
        try:
            os.killpg(self.p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def kill_all():
    for p in list(_PROCS):
        try:
            p.stop(grace=5)
        except Exception:
            pass


atexit.register(kill_all)


def port_free(port):
    import socket

    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def wait_http(url, timeout=60, proc=None):
    import requests

    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc is not None and not proc.alive():
            raise RuntimeError(f"{proc.name} exited early; see {proc.logpath}")
        try:
            if requests.get(url, timeout=2).status_code < 500:
                return
        except Exception:
            pass
        time.sleep(0.3)
    raise RuntimeError(f"timeout waiting for {url}")


# ---------------------------------------------------------------- memory sampling

_FP_RE = re.compile(r"Footprint:\s+([\d.]+)\s+(KB|MB|GB)")
_HAVE_FOOTPRINT = os.path.exists("/usr/bin/footprint")


def footprint_mb(pid):
    """macOS phys_footprint (includes compressed pages) via /usr/bin/footprint; falls back to psutil USS."""
    if _HAVE_FOOTPRINT:
        try:
            o = subprocess.run(
                ["footprint", str(pid)], capture_output=True, text=True, timeout=8
            ).stdout
            m = _FP_RE.search(o)
            if m:
                return (
                    float(m.group(1))
                    * {"KB": 1 / 1024, "MB": 1, "GB": 1024}[m.group(2)]
                )
        except Exception:
            pass
        return None
    try:
        return psutil.Process(pid).memory_full_info().uss / 1e6
    except Exception:
        return None


class Sampler:
    """Samples RSS of pid (+children) at `hz`, footprint of pid at 1 Hz; enforces the RSS watchdog."""

    def __init__(self, proc, csv_path, hz=20, kill_mb=KILL_MB):
        self.proc, self.pid, self.csv_path, self.hz, self.kill_mb = (
            proc,
            proc.pid,
            csv_path,
            hz,
            kill_mb,
        )
        self.rows = []  # (t_abs, rss_mb, child_rss_mb, nchild)
        self.fps = []  # (t_abs, footprint_mb)
        self.killed_at_mb = None
        self._stop = threading.Event()
        self.t0 = time.time()
        self.cpu_pct = 0.0

    def start(self):
        self._p = psutil.Process(self.pid)
        self._p.cpu_percent(None)
        self._th = threading.Thread(target=self._loop, daemon=True)
        self._fth = threading.Thread(target=self._floop, daemon=True)
        self._th.start()
        self._fth.start()
        return self

    def _floop(self):
        while not self._stop.is_set():
            v = footprint_mb(self.pid)
            if v is not None:
                self.fps.append((time.time(), v))
            self._stop.wait(0.8)

    def _loop(self):
        n = 0
        while not self._stop.is_set():
            try:
                rss = self._p.memory_info().rss
                ch, nch = 0, 0
                for c in self._p.children(recursive=True):
                    try:
                        ch += c.memory_info().rss
                        nch += 1
                    except Exception:
                        pass
                self.rows.append((time.time(), rss / 1e6, ch / 1e6, nch))
                n += 1
                if n % self.hz == 0:
                    self.cpu_pct = self._p.cpu_percent(None)
                if (rss + ch) / 1e6 > self.kill_mb and self.killed_at_mb is None:
                    self.killed_at_mb = round((rss + ch) / 1e6, 1)
                    self.proc.kill()
            except psutil.NoSuchProcess:
                break
            except Exception:
                pass
            self._stop.wait(1.0 / self.hz)

    def stop(self):
        self._stop.set()
        for t in (self._th, self._fth):
            t.join(timeout=10)
        Path(self.csv_path).parent.mkdir(parents=True, exist_ok=True)
        fi = 0
        with open(self.csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(
                ["t_s", "rss_mb", "children_rss_mb", "nchildren", "footprint_mb"]
            )
            for t, r, c, n in self.rows:
                fp = ""
                while fi < len(self.fps) and self.fps[fi][0] <= t:
                    fp = f"{self.fps[fi][1]:.1f}"
                    fi += 1
                w.writerow([f"{t - self.t0:.2f}", f"{r:.1f}", f"{c:.1f}", n, fp])

    def baseline(self, t_end, window=2.0):
        rs = [r for t, r, c, n in self.rows if t_end - window <= t <= t_end]
        fps = [v for t, v in self.fps if t <= t_end]
        return (statistics.median(rs) if rs else float("nan")), (
            fps[-1] if fps else float("nan")
        )

    def window_metrics(self, t_base, t_start, t_work_end, t_end):
        """Baseline at t_base; peaks over [t_start, t_end]; settled = last footprint/RSS before t_end."""
        base_rss, base_fp = self.baseline(t_base)
        rows = [x for x in self.rows if t_start <= x[0] <= t_end]
        peak = max((x[1] for x in rows), default=float("nan"))
        peak_children = max((x[2] for x in rows), default=0.0)
        peak_total = max((x[1] + x[2] for x in rows), default=float("nan"))
        fps = [v for t, v in self.fps if t_start <= t <= t_end]
        settled_fp = [v for t, v in self.fps if t <= t_end]
        last = [x[1] for x in self.rows if t_end - 3 <= x[0] <= t_end]
        r = lambda v: round(v, 1) if v == v else None
        return {
            "baseline_rss_mb": r(base_rss),
            "baseline_footprint_mb": r(base_fp),
            "peak_rss_mb": r(peak),
            "peak_delta_mb": r(peak - base_rss),
            "peak_children_rss_mb": r(peak_children),
            "peak_total_rss_mb": r(peak_total),
            "peak_footprint_mb": r(max(fps)) if fps else None,
            "settled_footprint_mb": r(settled_fp[-1]) if settled_fp else None,
            "settled_footprint_delta_mb": (
                r(settled_fp[-1] - base_fp)
                if settled_fp and base_fp == base_fp
                else None
            ),
            "settled_rss_mb": r(statistics.median(last)) if last else None,
            "work_s": round(t_work_end - t_start, 2),
            "killed_at_mb": self.killed_at_mb,
        }


# ---------------------------------------------------------------- db / redis


def admin_conn():
    c = psycopg2.connect(PG_ADMIN_DSN)
    c.autocommit = True
    return c


def create_db(name):
    assert re.fullmatch(
        r"memprof_[a-z0-9_]+", name
    ), name  # never touch any other database
    c = admin_conn()
    cur = c.cursor()
    cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    cur.execute(f'CREATE DATABASE "{name}"')
    c.close()


def drop_db(name):
    assert re.fullmatch(r"memprof_[a-z0-9_]+", name), name
    c = admin_conn()
    c.cursor().execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    c.close()


def rds():
    r = redis.Redis.from_url(REDIS_URL)
    assert r.connection_pool.connection_kwargs["db"] == 15
    return r


def flush_redis():
    rds().flushdb()  # logical db 15 only


def flush_ratelimits():
    r = rds()
    for k in r.scan_iter("ratelimit:*"):
        r.delete(k)


def summarize(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return {
        "median": round(statistics.median(vals), 2),
        "min": round(min(vals), 2),
        "max": round(max(vals), 2),
        "n": len(vals),
    }
