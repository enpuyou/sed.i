#!/usr/bin/env python
# ruff: noqa: E402, E731, E741
"""Black-box memory benchmark for the sed.i backend. See README.md.

usage: python scripts/memprof/run_benchmark.py --backend-dir <dir> --label before|after --out <results.json>
                                                [--scenarios S1,S2,...]
Run with the backend .venv interpreter; the target backend dir is used as cwd for every
subprocess so `app` resolves to that copy.
"""

import sys

sys.dont_write_bytecode = True
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C  # noqa: E402
import seed as S  # noqa: E402

PY = sys.executable
BASE = f"http://127.0.0.1:{C.API_PORT}"
SIZE_KEY = {"100KB": "100k", "1MB": "1m", "5MB": "5m", "20MB": "20m", "50MB": "50m"}
SUMMARY_KEYS = [
    "peak_delta_mb",
    "peak_rss_mb",
    "peak_footprint_mb",
    "settled_footprint_mb",
    "settled_footprint_delta_mb",
    "settled_rss_mb",
    "baseline_rss_mb",
    "baseline_footprint_mb",
    "peak_children_rss_mb",
    "wall_s",
]

METRIC_DEFS = {
    "baseline_rss_mb": "psutil RSS of the target process (parent only), median of the 2 s before the scenario starts (after settle).",
    "peak_rss_mb": "max psutil RSS of the target process over the scenario plus the 30 s settle window, sampled at 20 Hz.",
    "peak_delta_mb": "peak_rss_mb - baseline_rss_mb (headline metric).",
    "peak_children_rss_mb": "max summed RSS of child processes (e.g. YOLO subprocess, celery beat child).",
    "peak_total_rss_mb": "max of parent RSS + children RSS at one instant (the value the 6 GB watchdog compares).",
    "baseline_footprint_mb / peak_footprint_mb / settled_footprint_mb": "macOS phys_footprint from /usr/bin/footprint <pid> at ~1 Hz "
    "(includes compressed pages; RSS under-reports idle processes on macOS). settled = last sample 30 s after the scenario finished "
    "(API: after the last request; worker: after the pipeline drained).",
    "settled_footprint_delta_mb": "settled_footprint_mb - baseline_footprint_mb (memory retained after the work).",
    "settled_rss_mb": "median RSS over the last 3 s of the settle window (affected by the macOS memory compressor).",
    "wall_s": "API scenarios: duration of the HTTP calls. Worker scenarios: first POST /content to the moment the pipeline is drained "
    "(items terminal, broker queue + unacked empty, worker CPU < 5 %), excluding the 10 s quiet-confirmation window.",
    "killed_at_mb": "set if the 6 GB watchdog killed the process (a result, not a failure).",
    "worker baseline": "worker scenarios first process one unmeasured 100KB article (warm-up: first-use imports), wait for drain + 10 s, then take the baseline.",
}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Ctx:
    pass


def sql(ctx, q, args=None, fetch=True):
    conn = psycopg2.connect(C.db_dsn(ctx.db))
    conn.autocommit = True
    try:
        cur = conn.cursor()
        cur.execute(q, args)
        return cur.fetchall() if fetch else None
    finally:
        conn.close()


def env(ctx, extra=None):
    return C.child_env(ctx.backend, ctx.db, extra)


def run_py(ctx, args, timeout=600):
    return subprocess.run(
        [PY] + args,
        cwd=ctx.backend,
        env=env(ctx),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def start_api(ctx, tag):
    p = C.Proc(
        "api",
        [
            PY,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(C.API_PORT),
            "--log-level",
            "warning",
        ],
        ctx.backend,
        env(ctx),
        ctx.work / f"api_{tag}.log",
    )
    p.start()
    C.wait_http(f"{BASE}/health", 90, p)
    return p


def login(ctx, user_key):
    C.flush_ratelimits()
    r = requests.post(
        f"{BASE}/auth/login",
        data={"username": S.USERS[user_key][0], "password": S.PASSWORD},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def log_digest(text):
    lines = text.splitlines()
    err = [l for l in lines if "Traceback" in l or " ERROR" in l or "ERROR/" in l]
    retry = [l for l in lines if "Retry in" in l]
    return {
        "errors": len(err),
        "retries": len(retry),
        "first_errors": [e[:300] for e in err[:3]],
    }


def summarize_reps(reps):
    return {k: C.summarize([r.get(k) for r in reps]) for k in SUMMARY_KEYS}


# ------------------------------------------------------------------ API scenarios


def warm_api(ctx, token, dup_url=None):
    """Unmeasured warm-up so first-request costs (ORM mapper config, pools, lazy imports) are in the baseline.
    Never runs the heavy paths under test: only a LIMIT 1 list, the lists index, and (S1) a duplicate POST
    that is answered by the exact-match lookup (409)."""
    H = {"Authorization": f"Bearer {token}"}
    requests.get(f"{BASE}/content", params={"limit": 1}, headers=H, timeout=60)
    requests.get(f"{BASE}/lists", headers=H, timeout=60)
    if dup_url:
        C.flush_ratelimits()
        requests.post(f"{BASE}/content", json={"url": dup_url}, headers=H, timeout=60)


def api_scenario(
    ctx, sid, user_key, action, reps, cleanup=None, notes=None, dup_url=None
):
    res = {"kind": "api", "reps": [], "notes": notes or ""}
    ctx.results["scenarios"][sid] = res
    for rep in range(reps):
        log(f"{sid} rep {rep + 1}/{reps}")
        C.flush_redis()
        api = start_api(ctx, f"{sid}_{rep}")
        sm = C.Sampler(api, ctx.samples / f"{sid}_rep{rep}.csv", 20).start()
        rec = {"rep": rep}
        try:
            token = login(ctx, user_key)
            warm_api(ctx, token, dup_url)
            time.sleep(3.0)
            t_base = time.time()
            t_start = time.time()
            try:
                rec["http"] = action(ctx, token, rep)
            except Exception as e:  # e.g. watchdog killed the API
                rec["http"] = {"error": repr(e)[:300]}
            t_work = time.time()
            time.sleep(ctx.settle)
            t_end = time.time()
            rec.update(sm.window_metrics(t_base, t_start, t_work, t_end))
            rec["wall_s"] = round(t_work - t_start, 2)
            rec["api_log"] = log_digest(api.log_text())
            rec["samples_csv"] = f"{ctx.samples.name}/{sid}_rep{rep}.csv"
        finally:
            sm.stop()
            api.stop()
            if cleanup:
                cleanup(ctx, rep)
            C.flush_redis()
        res["reps"].append(rec)
        log(
            f"  peak_delta={rec.get('peak_delta_mb')} MB peak={rec.get('peak_rss_mb')} settled_fp={rec.get('settled_footprint_mb')} "
            f"wall={rec.get('wall_s')}s http={rec.get('http')}"
        )
    res["summary"] = summarize_reps(res["reps"])
    ctx.save()


def act_ingest(n):
    def f(ctx, token, rep):
        H = {"Authorization": f"Bearer {token}"}
        out = []
        for k in range(5):
            C.flush_ratelimits()
            t = time.time()
            r = requests.post(
                f"{BASE}/content",
                json={
                    "url": f"http://127.0.0.1:{C.PAGE_PORT}/page/100k/s1-{n}-{rep}-{k}"
                },
                headers=H,
                timeout=120,
            )
            out.append(
                {
                    "status": r.status_code,
                    "bytes": len(r.content),
                    "s": round(time.time() - t, 3),
                }
            )
        return {"requests": out, "statuses": sorted({o["status"] for o in out})}

    return f


def cleanup_ingest(n):
    def f(ctx, rep):
        sql(
            ctx,
            "delete from content_items where original_url like %s",
            ("%/page/100k/s1-%",),
            fetch=False,
        )

    return f


def act_get(path_fn, params=None):
    def f(ctx, token, rep):
        r = requests.get(
            BASE + path_fn(ctx),
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=300,
        )
        out = {"status": r.status_code, "bytes": len(r.content)}
        try:
            j = r.json()
            if isinstance(j, list):
                out["items"] = len(j)
            elif isinstance(j, dict):
                for key in ("items", "results", "data"):
                    if isinstance(j.get(key), list):
                        out["items"] = len(j[key])
                        out["total"] = j.get("total")
                        break
                out["shape"] = "object:" + ",".join(sorted(j)[:6])
        except Exception:
            pass
        return out

    return f


# ------------------------------------------------------------------ worker scenarios


def start_worker(ctx, tag):
    sched = ctx.work / f"celerybeat-{tag}"
    p = C.Proc(
        "worker",
        [
            PY,
            "-m",
            "celery",
            "-A",
            "app.core.celery_app",
            "worker",
            "--loglevel=info",
            "--pool=solo",
            "--beat",
            "-s",
            str(sched),
        ],
        ctx.backend,
        env(ctx),
        ctx.work / f"worker_{tag}.log",
    )
    p.start()
    t0 = time.time()
    while time.time() - t0 < 120:
        if not p.alive():
            raise RuntimeError(f"worker exited early, see {p.logpath}")
        if re.search(r"celery@\S+ ready\.", p.log_text()):
            return p
        time.sleep(0.5)
    raise RuntimeError("worker not ready in 120 s")


def post_url(ctx, url):
    C.flush_ratelimits()
    r = requests.post(
        f"{BASE}/content",
        json={"url": url},
        headers={"Authorization": f"Bearer {ctx.wtoken}"},
        timeout=120,
    )
    if r.status_code != 201:
        raise RuntimeError(f"POST /content -> {r.status_code} {r.text[:200]}")
    return r.json()["id"]


def wait_drained(ctx, worker, sm, ids, timeout, quiet=10):
    r = C.rds()
    t0 = time.time()
    qstart = None
    while time.time() - t0 < timeout:
        if sm.killed_at_mb:
            return "killed", None
        if not worker.alive():
            return "worker_died", None
        rows = sql(
            ctx,
            "select processing_status from content_items where id = any(%s::uuid[])",
            (ids,),
        )
        terminal = len(rows) == len(ids) and all(
            s in ("completed", "failed") for (s,) in rows
        )
        ok = (
            terminal
            and r.llen("celery") == 0
            and r.hlen("unacked") == 0
            and sm.cpu_pct < 5
        )
        if ok:
            qstart = qstart or time.time()
            if time.time() - qstart >= quiet:
                return "drained", qstart
        else:
            qstart = None
        time.sleep(1.0)
    return "timeout", None


def item_outcome(ctx, ids):
    rows = sql(
        ctx,
        "select i.processing_status, i.processing_error, i.word_count, i.content_type, i.embedding is not null, "
        "coalesce(length(i.full_text),0), (select count(*) from content_chunks c where c.content_item_id=i.id) "
        "from content_items i where i.id = any(%s::uuid[])",
        (ids,),
    )
    items = [
        {
            "processing_status": a,
            "processing_error": b,
            "word_count": c,
            "content_type": d,
            "embedding_present": e,
            "full_text_chars": f,
            "content_chunks": g,
        }
        for a, b, c, d, e, f, g in rows
    ]
    if len(items) == 1:
        return items[0]
    ws = sorted(i["word_count"] or 0 for i in items)
    return {
        "n_items": len(items),
        "n_completed": sum(i["processing_status"] == "completed" for i in items),
        "n_failed": sum(i["processing_status"] == "failed" for i in items),
        "n_with_error": sum(bool(i["processing_error"]) for i in items),
        "n_with_embedding": sum(i["embedding_present"] for i in items),
        "n_with_chunks": sum(i["content_chunks"] > 0 for i in items),
        "total_chunks": sum(i["content_chunks"] for i in items),
        "word_count_median": ws[len(ws) // 2] if ws else None,
        "processing_errors": sorted(
            {
                (i["processing_error"] or "")[:120]
                for i in items
                if i["processing_error"]
            }
        ),
    }


def worker_scenario(ctx, sid, urls_fn, reps, timeout, notes=None, n_urls=1):
    res = {"kind": "worker", "reps": [], "notes": notes or ""}
    ctx.results["scenarios"][sid] = res
    for rep in range(reps):
        log(f"{sid} rep {rep + 1}/{reps}")
        C.flush_redis()
        sql(ctx, "delete from content_items", fetch=False)
        worker = start_worker(ctx, f"{sid}_{rep}")
        sm = C.Sampler(worker, ctx.samples / f"{sid}_rep{rep}.csv", 20).start()
        rec = {"rep": rep}
        try:
            # warm-up (unmeasured): first-use imports; then settle baseline
            wid = post_url(
                ctx, f"http://127.0.0.1:{C.PAGE_PORT}/page/100k/warm-{sid}-{rep}"
            )
            st, _ = wait_drained(ctx, worker, sm, [wid], 180)
            rec["warmup"] = st
            time.sleep(10)
            t_base = time.time()
            wlog0 = worker.log_size()
            requests.post(f"http://127.0.0.1:{C.FAKE_PORT}/reset", timeout=10)
            urls = urls_fn(rep)
            t0 = time.time()
            ids = []
            for u in urls:
                ids.append(post_url(ctx, u))
            rec["submit_s"] = round(time.time() - t0, 2)
            st, qstart = wait_drained(ctx, worker, sm, ids, timeout)
            rec["pipeline_state"] = st
            t_work = qstart if qstart else time.time()
            time.sleep(ctx.settle)
            t_end = time.time()
            rec.update(sm.window_metrics(t_base, t0, t_work, t_end))
            rec["wall_s"] = round(t_work - t0, 2)
            rec["outcome"] = item_outcome(ctx, ids)
            rec["fake_openai"] = requests.get(
                f"http://127.0.0.1:{C.FAKE_PORT}/stats", timeout=10
            ).json()
            rec["worker_log"] = log_digest(worker.log_text(wlog0))
            rec["samples_csv"] = f"{ctx.samples.name}/{sid}_rep{rep}.csv"
        finally:
            sm.stop()
            worker.stop()
        res["reps"].append(rec)
        log(
            f"  state={rec.get('pipeline_state')} peak_delta={rec.get('peak_delta_mb')} MB peak={rec.get('peak_rss_mb')} "
            f"settled_fp={rec.get('settled_footprint_mb')} wall={rec.get('wall_s')}s killed={rec.get('killed_at_mb')} "
            f"outcome={rec.get('outcome')}"
        )
        ctx.save()
    res["summary"] = summarize_reps(res["reps"])
    ctx.save()


# ------------------------------------------------------------------ main


def source_hash(backend):
    h = hashlib.sha1()
    files = sorted(
        list((backend / "app").rglob("*.py"))
        + list((backend / "alembic" / "versions").glob("*.py"))
    )
    for f in files:
        h.update(str(f.relative_to(backend)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()[:16], len(files)


def git_info(backend, override):
    if override:
        return override
    try:
        g = lambda *a: subprocess.run(
            ["git", "-C", str(backend)] + list(a),
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        head = g("rev-parse", "--short", "HEAD")
        dirty = g("status", "--porcelain", "--", str(backend))
        return f"working-tree (HEAD {head} + uncommitted changes)" if dirty else head
    except Exception:
        return "unknown (not a git checkout; pass --commit)"


def host_info():
    def sc(k):
        try:
            return subprocess.run(
                ["sysctl", "-n", k], capture_output=True, text=True
            ).stdout.strip()
        except Exception:
            return None

    mem = sc("hw.memsize")
    return {
        "platform": platform.platform(),
        "macos": platform.mac_ver()[0],
        "machine": platform.machine(),
        "cpu": sc("machdep.cpu.brand_string"),
        "ncpu": os.cpu_count(),
        "ram_gb": round(int(mem) / 2**30, 1) if mem else None,
        "python": sys.version.split()[0],
        "python_executable": PY,
    }


def want(selected, sid):
    return selected is None or any(sid.startswith(s) for s in selected)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend-dir", required=True)
    ap.add_argument("--label", required=True, help="before|after (any [a-z0-9_]+)")
    ap.add_argument("--out", required=True)
    ap.add_argument(
        "--scenarios",
        default=None,
        help="comma list of ids or id prefixes, e.g. S1,S4_worker_article_1MB",
    )
    ap.add_argument(
        "--commit",
        default=None,
        help="override commit string (needed for a non-git snapshot)",
    )
    ap.add_argument(
        "--settle", type=int, default=30, help="settle window seconds (default 30)"
    )
    ap.add_argument(
        "--reps",
        type=int,
        default=None,
        help="cap repetitions per scenario (smoke tests)",
    )
    a = ap.parse_args()
    assert re.fullmatch(r"[a-z0-9_]+", a.label)

    ctx = Ctx()
    ctx.backend = Path(a.backend_dir).resolve()
    ctx.label, ctx.db, ctx.settle = a.label, f"memprof_{a.label}", a.settle
    out = Path(a.out).resolve()
    ctx.samples = out.with_suffix("")
    ctx.samples.mkdir(parents=True, exist_ok=True)
    for old in ctx.samples.glob("*.csv"):
        old.unlink()
    ctx.work = Path(tempfile.mkdtemp(prefix=f"memprof_{a.label}_"))
    selected = [s for s in a.scenarios.split(",")] if a.scenarios else None
    cap = lambda n: min(n, a.reps) if a.reps else n
    t_run = time.time()
    shash, nfiles = source_hash(ctx.backend)
    ctx.results = {
        "label": a.label,
        "commit": git_info(ctx.backend, a.commit),
        "source_hash": shash,
        "source_files_hashed": nfiles,
        "backend_dir": str(ctx.backend),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "host": host_info(),
        "metric_definitions": METRIC_DEFS,
        "settings": {
            "settle_s": a.settle,
            "sample_hz": 20,
            "watchdog_mb": C.KILL_MB,
            "api_port": C.API_PORT,
            "database": ctx.db,
            "redis": C.REDIS_URL,
            "scenarios_selected": selected,
        },
        "scenarios": {},
    }

    def save():
        ctx.results["total_runtime_s"] = round(time.time() - t_run, 1)
        out.write_text(json.dumps(ctx.results, indent=2))

    ctx.save = save

    for port in (C.API_PORT, C.FAKE_PORT, C.PAGE_PORT):
        if not C.port_free(port):
            sys.exit(f"port {port} already in use")
    chk = run_py(ctx, ["-c", "import app,sys;print(app.__file__)"], 60)
    app_file = chk.stdout.strip().splitlines()[-1] if chk.stdout.strip() else ""
    if not app_file.startswith(str(ctx.backend)):
        sys.exit(f"`app` resolves to {app_file!r}, not {ctx.backend}")
    ctx.results["app_file"] = app_file
    log(
        f"label={a.label} app={app_file} commit={ctx.results['commit']} work={ctx.work}"
    )

    db_created = False
    try:
        # ---- support servers
        fake = C.Proc(
            "fake_openai",
            [
                PY,
                "-m",
                "uvicorn",
                "fake_openai:app",
                "--app-dir",
                str(C.HERE),
                "--host",
                "127.0.0.1",
                "--port",
                str(C.FAKE_PORT),
                "--log-level",
                "warning",
            ],
            C.HERE,
            {"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"},
            ctx.work / "fake_openai.log",
        ).start()
        pages = C.Proc(
            "pageserver",
            [
                PY,
                "-m",
                "uvicorn",
                "pageserver:app",
                "--app-dir",
                str(C.HERE),
                "--host",
                "127.0.0.1",
                "--port",
                str(C.PAGE_PORT),
                "--log-level",
                "warning",
            ],
            C.HERE,
            {"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"},
            ctx.work / "pageserver.log",
        ).start()
        C.wait_http(f"http://127.0.0.1:{C.FAKE_PORT}/stats", 60, fake)
        C.wait_http(f"http://127.0.0.1:{C.PAGE_PORT}/health", 60, pages)

        # ---- database
        log(f"creating {ctx.db} + alembic upgrade heads")
        C.create_db(ctx.db)
        db_created = True
        C.flush_redis()
        r = run_py(ctx, ["-m", "alembic", "upgrade", "heads"], 600)
        if r.returncode != 0:
            raise RuntimeError(
                "alembic failed:\n" + r.stdout[-2000:] + r.stderr[-2000:]
            )
        h = run_py(
            ctx,
            [
                "-c",
                f"from app.core.security import get_password_hash as g; print('HASH:'+g({S.PASSWORD!r}))",
            ],
            120,
        )
        hashed = [l for l in h.stdout.splitlines() if l.startswith("HASH:")][-1][5:]
        need = set()
        for sid, key in (
            ("S1_ingest_n100", "u100"),
            ("S1_ingest_n500", "u500"),
            ("S1_ingest_n2000", "u2000"),
            ("S2", "u2000"),
            ("S3", "u2000"),
        ):
            if want(selected, sid):
                need.add(key)
        t = time.time()
        ctx.results["seed"] = S.seed_all(C.db_dsn(ctx.db), hashed, need, log)
        log(f"seeded in {time.time() - t:.0f}s")
        ver = sql(ctx, "select version()")[0][0]
        ctx.results["host"]["postgres"] = ver.split(",")[0]
        ctx.results["host"]["redis"] = C.rds().info().get("redis_version")
        ctx.save()

        # ---- API scenarios
        for n in (100, 500, 2000):
            sid = f"S1_ingest_n{n}"
            if want(selected, sid):
                api_scenario(
                    ctx,
                    sid,
                    f"u{n}",
                    act_ingest(n),
                    cap(3),
                    cleanup_ingest(n),
                    "5 sequential POST /content (new local URLs); API process memory.",
                    dup_url=f"http://127.0.0.1:{C.PAGE_PORT}/seed/u{n}/0",
                )
        if want(selected, "S2_recommended_n2000"):
            api_scenario(
                ctx,
                "S2_recommended_n2000",
                "u2000",
                act_get(lambda c: "/content/recommended", {"limit": 20}),
                cap(3),
                notes="GET /content/recommended?limit=20, 2000-item library.",
            )
        if want(selected, "S3"):
            lid = ctx.results["seed"]["u2000"]["list_id"]
            path = lambda c: f"/lists/{lid}/content"
            if want(selected, "S3_list_content_n2000"):
                api_scenario(
                    ctx,
                    "S3_list_content_n2000",
                    "u2000",
                    act_get(path),
                    cap(3),
                    notes="GET /lists/{id}/content with no query params (current frontend call); list holds all 2000 items.",
                )
            if want(selected, "S3b"):
                p = run_py(
                    ctx,
                    [
                        "-c",
                        "import json;from app.main import app;print('OAS:'+json.dumps(app.openapi()))",
                    ],
                    120,
                )
                params = []
                try:
                    oas = json.loads(
                        [l for l in p.stdout.splitlines() if l.startswith("OAS:")][-1][
                            4:
                        ]
                    )
                    params = [
                        x["name"]
                        for x in oas["paths"]["/lists/{list_id}/content"]["get"].get(
                            "parameters", []
                        )
                        if x.get("in") == "query"
                    ]
                except Exception as e:
                    params = [f"error:{e!r}"]
                lim = next(
                    (x for x in ("limit", "page_size", "per_page") if x in params), None
                )
                sid = "S3b_list_content_n2000_limit50"
                if not lim:
                    ctx.results["scenarios"][sid] = {
                        "kind": "api",
                        "skipped": "endpoint has no limit parameter at this commit",
                        "query_params_found": params,
                    }
                    log(f"{sid} skipped: query params {params}")
                else:
                    q = {lim: 50}
                    off = next((x for x in ("offset", "skip") if x in params), None)
                    if off:
                        q[off] = 0
                    api_scenario(
                        ctx,
                        sid,
                        "u2000",
                        act_get(path, q),
                        cap(3),
                        notes=f"GET /lists/{{id}}/content with {q}",
                    )
                    ctx.results["scenarios"][sid]["query_params_found"] = params
                ctx.save()

        # ---- worker scenarios (empty library: isolates the ingest pipeline from beat backfill noise)
        worker_ids = [f"S4_worker_article_{k}" for k in SIZE_KEY if k != "50MB"] + [
            "S5_worker_article_50MB",
            "S6_worker_nonhtml",
            "S7_regression_small",
        ]
        if any(want(selected, s) for s in worker_ids):
            sql(ctx, "delete from content_items", fetch=False)
            C.flush_redis()
            api = start_api(ctx, "worker_phase")
            ctx.wtoken = login(ctx, "uw")
            for size in ("100KB", "1MB", "5MB", "20MB"):
                sid = f"S4_worker_article_{size}"
                if want(selected, sid):
                    worker_scenario(
                        ctx,
                        sid,
                        lambda rep, size=size: [
                            f"http://127.0.0.1:{C.PAGE_PORT}/page/{SIZE_KEY[size]}/{size}-{rep}"
                        ],
                        cap(2 if size == "20MB" else 3),
                        900,
                        notes=f"1 synthetic {size} article via POST /content; worker (parent) memory.",
                    )
            if want(selected, "S5_worker_article_50MB"):
                worker_scenario(
                    ctx,
                    "S5_worker_article_50MB",
                    lambda rep: [f"http://127.0.0.1:{C.PAGE_PORT}/page/50m/50MB-{rep}"],
                    cap(1),
                    1500,
                    notes="1 synthetic 50 MB article; 6 GB watchdog.",
                )
            if want(selected, "S6_worker_nonhtml"):
                worker_scenario(
                    ctx,
                    "S6_worker_nonhtml",
                    lambda rep: [
                        f"http://127.0.0.1:{C.PAGE_PORT}/file/archive.zip?mb=100"
                    ],
                    cap(1),
                    900,
                    notes="100 MB application/zip served streamed (Content-Length set); worker memory + outcome.",
                )
            if want(selected, "S7_regression_small"):
                worker_scenario(
                    ctx,
                    "S7_regression_small",
                    lambda rep: [
                        f"http://127.0.0.1:{C.PAGE_PORT}/page/100k/s7-{rep}-{i}"
                        for i in range(20)
                    ],
                    cap(2),
                    900,
                    notes="20 distinct 100KB articles saved back to back; wall = first POST to drained.",
                )
            api.stop()
    except BaseException:
        ctx.results["fatal_error"] = traceback.format_exc()
        log(ctx.results["fatal_error"])
        raise
    finally:
        C.kill_all()
        try:
            if db_created:
                C.drop_db(ctx.db)
            C.flush_redis()
        except Exception as e:
            log("cleanup error", e)
        ctx.results["cleanup"] = (
            "scratch db dropped, redis db15 flushed, processes stopped"
        )
        save()
        shutil.rmtree(ctx.work, ignore_errors=True)
        log(f"done in {time.time() - t_run:.0f}s -> {out}")


if __name__ == "__main__":
    main()
